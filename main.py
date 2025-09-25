import asyncio
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
    
    def get_api_token(self, environment: str = "prod") -> str:
        """Get API token for specific environment"""
        token_map = {
            "prod": self.prod_api_token or self.api_service_token,
            "staging": self.staging_api_token or self.api_service_token,
            "dev": self.dev_api_token or self.api_service_token
        }
        return token_map.get(environment, self.api_service_token)

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
        headers["Authorization"] = f"Bearer {self.auth_config.dch_api_token}"
        
        # Add service identification header
        headers["X-MCP-Service"] = "claude-mcp-server"
        headers["X-MCP-Version"] = "1.0.0"
        return headers
    
    def _get_platform_api_headers(self, use_service_token: bool = True) -> Dict[str, str]:
        """Generate headers for Platform API requests"""
        headers = {"Content-Type": "application/json"}
        
        # Use service token for server-to-server requests
        headers["Authorization"] = f"Bearer {self.auth_config.platform_api_token}"
        
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
            if tool_name == "get_user_data":
                return await self._get_user_data(arguments)
            elif tool_name == "get_user_orders":
                return await self._get_user_orders(arguments)
            elif tool_name == "update_user_settings":
                return await self._update_user_settings(arguments)
            elif tool_name == "call_lambda_api":
                return await self._call_lambda_api(arguments)
            elif tool_name == "search_users":
                return await self._search_users(arguments)
            else:
                return {"error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            logger.error(f"Error in tool call {tool_name}: {e}")
            return {"error": str(e)}
    
    async def _get_user_data(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get user data from main API"""
        user_id = args.get("user_id")
        user_token = args.get("token")  # Optional user token
        
        if not user_id:
            return {"error": "user_id is required"}
        
        url = f"{self.api_base_url}/users/{user_id}"
        headers = self._get_api_headers(user_token=user_token)
        
        result = await self._make_api_request("GET", url, headers)
        
        if "error" in result:
            return {"content": [{"type": "text", "text": json.dumps(result)}]}
        
        return {"content": [{"type": "text", "text": json.dumps(result)}]}
    
    async def _get_user_orders(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get user orders"""
        user_id = args.get("user_id")
        user_token = args.get("token")
        limit = args.get("limit", 10)
        
        if not user_id:
            return {"error": "user_id is required"}
        
        url = f"{self.api_base_url}/users/{user_id}/orders"
        headers = self._get_api_headers(user_token=user_token)
        params = {"limit": limit}
        
        result = await self._make_api_request("GET", url, headers, params=params)
        return {"content": [{"type": "text", "text": json.dumps(result)}]}
    
    async def _update_user_settings(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Update user settings"""
        user_id = args.get("user_id")
        settings = args.get("settings")
        user_token = args.get("token")
        
        if not user_id or not settings:
            return {"error": "user_id and settings are required"}
        
        url = f"{self.api_base_url}/users/{user_id}/settings"
        headers = self._get_api_headers(user_token=user_token)
        
        result = await self._make_api_request("PUT", url, headers, json_data=settings)
        
        if "error" not in result:
            return {"content": [{"type": "text", "text": "Settings updated successfully"}]}
        else:
            return {"content": [{"type": "text", "text": json.dumps(result)}]}
    
    async def _call_lambda_api(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Call Lambda API via API Gateway"""
        endpoint = args.get("endpoint")
        payload = args.get("payload", {})
        user_token = args.get("token")
        
        if not endpoint:
            return {"error": "endpoint is required"}
        
        url = f"{self.api_gateway_url}/{endpoint.lstrip('/')}"
        headers = self._get_gateway_headers(user_token=user_token)
        
        result = await self._make_api_request("POST", url, headers, json_data=payload)
        return {"content": [{"type": "text", "text": json.dumps(result)}]}
    
    async def _search_users(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Search users - admin only function using service token"""
        query = args.get("query")
        limit = args.get("limit", 10)
        
        if not query:
            return {"error": "query is required"}
        
        url = f"{self.api_base_url}/admin/users/search"
        headers = self._get_api_headers(use_service_token=True)  # Use service token for admin functions
        params = {"q": query, "limit": limit}
        
        result = await self._make_api_request("GET", url, headers, params=params)
        return {"content": [{"type": "text", "text": json.dumps(result)}]}
    
    async def read_resource(self, uri: str) -> Dict[str, Any]:
        """Read resource from URI"""
        if uri.startswith("webapp://current-state/"):
            user_id = uri.split("/")[-1]
            try:
                url = f"{self.api_base_url}/users/{user_id}/current-state"
                headers = self._get_api_headers(use_service_token=True)
                
                result = await self._make_api_request("GET", url, headers)
                return {"content": json.dumps(result)}
            except Exception as e:
                logger.error(f"Error reading resource: {e}")
                return {"content": json.dumps({"error": str(e)})}
        
        return {"content": json.dumps({"error": "Resource not found"})}

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