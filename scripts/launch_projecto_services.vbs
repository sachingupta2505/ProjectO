' Runs the ProjectO launcher without leaving a visible PowerShell window at user logon.
CreateObject("Wscript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\projectO\scripts\start_projecto_services.ps1""", 0, False
