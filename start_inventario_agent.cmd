@echo off
cd /d %~dp0

if not exist venv (
  py -m venv venv
)

call venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements_local_agent.txt

set INVENTARIO_AGENT_PORT=8788
set INVENTARIO_AGENT_HOST=0.0.0.0
set INVENTARIO_FOTOS_SCRIPT=%~dp0gemini_selenium_cli.py
set INVENTARIO_AGENT_TOKEN=BmFclqQdWkKjArLIYvakHG426BuLDUtJA0zVG5DJOgjZTWSEVa_i0hxiyXskSHUi

python inventario_local_agent.py
pause
