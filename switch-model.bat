@echo off
REM GenAI Stack CLI launcher (CMD)
REM Usage: switch-model.bat status
REM        switch-model.bat use genai

cd /d "%~dp0"
uv run python cli.py %*
