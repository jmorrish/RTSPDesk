@echo off
cd /d "%~dp0"
pyw -3 run.py
if errorlevel 1 (
  echo Python 3 with Tcl/Tk is required. See README.md for setup instructions.
  pause
)
