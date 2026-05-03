"""
run_server.py — Entry point for SelfConnect Vision Server.
Usage: python run_server.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from vision_server.config import HOST, PORT

if __name__ == "__main__":
    uvicorn.run(
        "vision_server.main:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )
