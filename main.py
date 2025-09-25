import asyncio
from datetime import datetime
import os
import json
from typing import Dict, Any, List, Optional
import aiohttp
import logging
from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
import uvicorn
from functools import lru_cache

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="WebApp MCP Server", version="1.0.0")
security = HTTPBearer()

class ToolCallRequest(BaseModel):
    name: str
    arguments: Dict[str, Any]

class ResourceRequest(BaseModel):
    uri: str

class AuthConfig:
    """Configuration for authentication tokens"""
    
    def __init__(self):
        # Load environment variables
        self.mcp_server_token = os.getenv("MCP_SERVER_TOKEN")
        self.dch_api_token = os.getenv("DCH_API_TOKEN")
        self.platform_api_token = os.getenv("PLATFORM_API_TOKEN")
        
        # Settings
        self.token_refresh_threshold = int(os.getenv("TOKEN_REFRESH_THRESHOLD", "300"))
        self.max_retry_attempts = int(os.getenv("MAX_RETRY_ATTEMPTS", "3"))
        
        # Validate required tokens
        self._validate_tokens()
    
    def _validate_tokens(self):
        """Validate that required tokens are present"""
        missing_tokens = []
        
        if not self.mcp_server_token:
            missing_tokens.append("MCP_SERVER_TOKEN")
        if not self.dch_api_token:
            missing_tokens.append("DCH_API_TOKEN")
        if not self.platform_api_token:
            missing_tokens.append("PLATFORM_API_TOKEN")
        
        if missing_tokens:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_tokens)}")
    

class MCPServer:
    def __init__(self):
        self.dch_api_url = os.getenv("DCH_API_URL")
        self.platform_api_url = os.getenv("PLATFORM_API_URL")
        self.auth_config = AuthConfig()
        
        if not self.dch_api_url:
            raise ValueError("DCH_API_URL environment variable is required")
        if not self.platform_api_url:
            raise ValueError("PLATFORM_API_URL environment variable is required")
    
    def _get_dch_api_headers(self, use_service_token: bool = True) -> Dict[str, str]:
        """Generate headers for API requests"""
        headers = {"Content-Type": "application/json"}
        
        # Use service token for server-to-server requests
        headers["authorization"] = f"{self.auth_config.dch_api_token}"
    
        # Add service identification header
        #headers["X-MCP-Service"] = "claude-mcp-server"
        #headers["X-MCP-Version"] = "1.0.0"
        return headers
    
    def _get_platform_api_headers(self, use_service_token: bool = True) -> Dict[str, str]:
        """Generate headers for Platform API requests"""
        headers = {"Content-Type": "application/json"}
        
        # Use service token for server-to-server requests
        headers["Authorization"] = f"{self.auth_config.platform_api_token}"
        
        # Add service identification header
        headers["X-MCP-Service"] = "claude-mcp-server"
        headers["X-MCP-Version"] = "1.0.0"
        return headers

    async def _make_api_request(
        self, 
        method: str, 
        url: str, 
        headers: Dict[str, str],
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make API request with retry logic"""
        
        for attempt in range(self.auth_config.max_retry_attempts):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.request(
                        method=method,
                        url=url,
                        headers=headers,
                        json=json_data,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        
                        if response.status == 401:
                            logger.warning(f"Authentication failed for {url} (attempt {attempt + 1})")
                            if attempt == self.auth_config.max_retry_attempts - 1:
                                return {"error": "Authentication failed after all retry attempts"}
                            continue
                        
                        if response.status >= 400:
                            error_text = await response.text()
                            logger.error(f"API request failed: {response.status} - {error_text}")
                            return {"error": f"API request failed: {response.status}"}
                        
                        try:
                            return await response.json()
                        except:
                            # If response is not JSON, return the text
                            text_response = await response.text()
                            return {"data": text_response}
                            
            except asyncio.TimeoutError:
                logger.error(f"Request timeout for {url} (attempt {attempt + 1})")
                if attempt == self.auth_config.max_retry_attempts - 1:
                    return {"error": "Request timeout after all retry attempts"}
            except Exception as e:
                logger.error(f"Request error for {url}: {e} (attempt {attempt + 1})")
                if attempt == self.auth_config.max_retry_attempts - 1:
                    return {"error": f"Request failed: {str(e)}"}
        
        return {"error": "Max retry attempts exceeded"}
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tool calls with proper authentication"""
        try:
            if tool_name == "get_ships":
                return await self._get_ships(arguments)
            elif tool_name == "get_ship_emissions":
                return await self._get_ship_emissions(arguments)
            else:
                return {"error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            logger.error(f"Error in tool call {tool_name}: {e}")
            return {"error": str(e)}
        
    async def _get_ships(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get ships from DCH API"""
        url = f"{self.dch_api_url}/assets?include_internal=true"
        headers = self._get_dch_api_headers()
        logger.info(f"Fetching ships from {url}")
        result = await self._make_api_request("GET", url, headers)
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    async def _get_ship_emissions(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get ship emissions from DCH API with time period filtering"""
        
        # Validate required argument
        ship_id = args.get("asset_id") or args.get("ship_id")
        if not ship_id:
            return {"error": "Missing asset_id or ship_id argument"}
        
        # Handle time period parameters
        start_date = args.get("start")
        end_date = args.get("end")
        
        # Set default to year-to-date if no dates provided
        if not start_date and not end_date:
            current_year = datetime.now().year
            start_date = f"{current_year}-01-01T00:00:00Z"
            end_date = datetime.now().isoformat() + "Z"
            logger.info(f"Using year-to-date default: {start_date} to {end_date}")
        
        # Validate and format dates
        try:
            if start_date:
                # Validate ISO format and ensure Z suffix for UTC
                start_dt = datetime.fromisoformat(start_date.replace('Z', ''))
                start_date = start_dt.isoformat()
            
            if end_date:
                # Validate ISO format and ensure Z suffix for UTC
                end_dt = datetime.fromisoformat(end_date.replace('Z', ''))
                end_date = end_dt.isoformat()
                
            # Ensure start is before end
            if start_date and end_date:
                if start_dt >= end_dt:
                    return {"error": "start date must be before end date"}
                    
        except ValueError as e:
            return {"error": f"Invalid date format. Use ISO format (YYYY-MM-DDTHH:MM:SS): {str(e)}"}
        
        # Build URL with query parameters
        url = f"{self.dch_api_url}/voyages/emissions"
        
        # Build query parameters
        params = {"asset_ids": ship_id}
        
        if start_date:
            params["start"] = start_date
        if end_date:
            params["end"] = end_date
        
        # Convert params to query string
        query_params = "&".join([f"{k}={v}" for k, v in params.items()])
        full_url = f"{url}?{query_params}"
        
        headers = self._get_dch_api_headers()
        
        logger.info(f"Fetching ship emissions from {full_url}")
        logger.debug(f"Headers: {headers}")
        logger.info(f"Time period: {start_date or 'N/A'} to {end_date or 'N/A'}")
        
        result = await self._make_api_request("GET", full_url, headers)
        
        # Add metadata about the query to the response
        if "error" not in result:
            query_info = {
                "ship_id": ship_id,
                "time_period": {
                    "start": start_date,
                    "end": end_date,
                    "is_year_to_date": not args.get("start") and not args.get("end")
                },
                "query_timestamp": datetime.now().isoformat() + "Z"
            }
            
            # Wrap the result with query metadata
            response_data = {
                "query_info": query_info,
                "emissions_data": result
            }
            
            return {"content": [{"type": "text", "text": json.dumps(response_data, indent=2)}]}
        else:
            return {"content": [{"type": "text", "text": json.dumps(result)}]}

# Initialize MCP server
mcp_server = MCPServer()

# Authentication middleware
async def authenticate_mcp_request(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Authenticate incoming MCP requests"""
    if credentials.credentials != mcp_server.auth_config.mcp_server_token:
        raise HTTPException(status_code=401, detail="Invalid MCP server token")
    return credentials.credentials

@app.post("/tools/call")
async def call_tool_endpoint(
    request: ToolCallRequest,
    token: str = Depends(authenticate_mcp_request)
):
    """Handle tool call requests with authentication"""
    logger.info(f"Tool call: {request.name} with args: {list(request.arguments.keys())}")
    result = await mcp_server.call_tool(request.name, request.arguments)
    return result

@app.post("/resources/read")
async def read_resource_endpoint(
    request: ResourceRequest,
    token: str = Depends(authenticate_mcp_request)
):
    """Handle resource read requests with authentication"""
    logger.info(f"Resource read: {request.uri}")
    result = await mcp_server.read_resource(request.uri)
    return result

@app.get("/health")
async def health_check():
    """Health check endpoint (no auth required)"""
    return {
        "status": "healthy",
        "service": "mcp-server",
        "version": "1.0.0",
        "auth_configured": bool(mcp_server.auth_config.mcp_server_token)
    }

@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "MCP Server is running", "version": "1.0.0"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", "3001"))
    log_level = os.getenv("LOG_LEVEL", "info").lower()
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=port,
        log_level=log_level,
        reload=os.getenv("DEBUG", "false").lower() == "true"
    )