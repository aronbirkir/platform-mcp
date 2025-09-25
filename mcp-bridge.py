#!/usr/bin/env python3
# mcp-bridge-standalone.py - No external dependencies

import sys
import json
import urllib.request
import urllib.parse
import urllib.error
import os
from typing import Dict, Any

class MCPHTTPBridge:
    def __init__(self):
        self.server_url = os.getenv("MCP_SERVER_URL", "http://localhost:3001")
        self.token = os.getenv("MCP_SERVER_TOKEN", "local-dev-token")
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}"
        }
    
    def make_request(self, path: str, method: str = "POST", data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Make HTTP request using only stdlib"""
        url = f"{self.server_url}{path}"
        
        try:
            # Prepare request data
            if data:
                json_data = json.dumps(data).encode('utf-8')
            else:
                json_data = None
            
            # Create request
            req = urllib.request.Request(
                url, 
                data=json_data, 
                headers=self.headers,
                method=method
            )
            
            # Make request
            with urllib.request.urlopen(req, timeout=30) as response:
                response_data = response.read().decode('utf-8')
                return json.loads(response_data)
                
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8') if e.fp else str(e)
            return {"error": f"HTTP {e.code}: {error_body}"}
        except urllib.error.URLError as e:
            return {"error": f"Connection error: {str(e)}"}
        except Exception as e:
            return {"error": f"Request failed: {str(e)}"}
    
    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call MCP server tool via HTTP"""
        payload = {"name": name, "arguments": arguments}
        return self.make_request("/tools/call", "POST", payload)
    
    def read_resource(self, uri: str) -> Dict[str, Any]:
        """Read resource via HTTP"""
        payload = {"uri": uri}
        return self.make_request("/resources/read", "POST", payload)
    
    def list_tools(self) -> Dict[str, Any]:
        """List available tools"""
        return {
            "tools": [
                {
                    "name": "get_user_data",
                    "description": "Get user profile and account information",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "user_id": {"type": "string", "description": "User ID"},
                            "token": {"type": "string", "description": "User auth token (optional)"}
                        },
                        "required": ["user_id"]
                    }
                },
                {
                    "name": "get_user_orders",
                    "description": "Get user's recent orders",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "user_id": {"type": "string", "description": "User ID"},
                            "limit": {"type": "number", "description": "Number of orders", "default": 10},
                            "token": {"type": "string", "description": "User auth token (optional)"}
                        },
                        "required": ["user_id"]
                    }
                },
                {
                    "name": "update_user_settings",
                    "description": "Update user preferences and settings",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "user_id": {"type": "string", "description": "User ID"},
                            "settings": {"type": "object", "description": "Settings to update"},
                            "token": {"type": "string", "description": "User auth token (optional)"}
                        },
                        "required": ["user_id", "settings"]
                    }
                },
                {
                    "name": "test_connection",
                    "description": "Test connection to APIs and return status",
                    "inputSchema": {
                        "type": "object",
                        "properties": {}
                    }
                }
            ]
        }
    
    def list_resources(self) -> Dict[str, Any]:
        """List available resources"""
        return {
            "resources": [
                {
                    "uri": "webapp://current-state/{user_id}",
                    "name": "Current User State",
                    "description": "Get current state for a specific user",
                    "mimeType": "application/json"
                }
            ]
        }

def send_response(response: Dict[str, Any]):
    """Send response to Claude Desktop via stdout"""
    try:
        print(json.dumps(response), flush=True)
    except Exception as e:
        error_response = {
            "jsonrpc": "2.0",
            "error": {"code": -1, "message": f"Response error: {str(e)}"}
        }
        print(json.dumps(error_response), flush=True)

def main():
    """Main MCP bridge loop"""
    bridge = MCPHTTPBridge()
    
    # Send startup message to stderr
    print("MCP Bridge starting (standalone version)...", file=sys.stderr, flush=True)
    print(f"Server URL: {bridge.server_url}", file=sys.stderr, flush=True)
    
    try:
        # Read from stdin line by line
        for line in sys.stdin:
            try:
                line = line.strip()
                if not line:
                    continue
                
                # Parse JSON-RPC request
                try:
                    request = json.loads(line)
                except json.JSONDecodeError as e:
                    error_response = {
                        "jsonrpc": "2.0",
                        "error": {"code": -32700, "message": f"Parse error: {str(e)}"}
                    }
                    send_response(error_response)
                    continue
                
                method = request.get("method")
                params = request.get("params", {})
                request_id = request.get("id")
                
                print(f"Processing: {method}", file=sys.stderr, flush=True)
                
                # Handle MCP methods
                if method == "initialize":
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {
                                "tools": {},
                                "resources": {}
                            },
                            "serverInfo": {
                                "name": "webapp-mcp-bridge-standalone",
                                "version": "1.0.0"
                            }
                        }
                    }
                    send_response(response)
                
                elif method == "notifications/initialized":
                    print("MCP initialized successfully", file=sys.stderr, flush=True)
                    continue
                
                elif method == "tools/list":
                    tools = bridge.list_tools()
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": tools
                    }
                    send_response(response)
                
                elif method == "tools/call":
                    tool_name = params.get("name")
                    tool_args = params.get("arguments", {})
                    
                    print(f"Calling tool: {tool_name}", file=sys.stderr, flush=True)
                    
                    result = bridge.call_tool(tool_name, tool_args)
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": result
                    }
                    send_response(response)
                
                elif method == "resources/list":
                    resources = bridge.list_resources()
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": resources
                    }
                    send_response(response)
                
                elif method == "resources/read":
                    uri = params.get("uri")
                    result = bridge.read_resource(uri)
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": result
                    }
                    send_response(response)
                
                else:
                    error_response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"}
                    }
                    send_response(error_response)
                    
            except Exception as e:
                print(f"Error processing request: {e}", file=sys.stderr, flush=True)
                error_response = {
                    "jsonrpc": "2.0",
                    "id": request.get("id") if 'request' in locals() else None,
                    "error": {"code": -1, "message": str(e)}
                }
                send_response(error_response)
    
    except KeyboardInterrupt:
        print("Bridge interrupted", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr, flush=True)
    finally:
        print("MCP Bridge shutting down", file=sys.stderr, flush=True)

if __name__ == "__main__":
    main()