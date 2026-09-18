@echo off
rem Creates (or replaces) a desktop shortcut to rosarium.bat. Run it once;
rem run it again after renaming the shortcut below or changing the icon.
rem
rem   name : SHORTCUT_NAME below (the file name of the .lnk is what the desktop shows)
rem   icon : launch\rosarium.ico when it exists, the default .bat icon otherwise.
rem          Windows wants a .ico file — convert a PNG first.

setlocal
set "SHORTCUT_NAME=rosarium"
set "LAUNCH=%~dp0"
set "ICON=%LAUNCH%rosarium.ico"

rem A .lnk is written through the WScript.Shell COM object, from PowerShell.
rem GetFolderPath('Desktop') follows a OneDrive-redirected desktop, which
rem %USERPROFILE%\Desktop does not.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$desktop = [Environment]::GetFolderPath('Desktop');" ^
  "$lnk = Join-Path $desktop '%SHORTCUT_NAME%.lnk';" ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk);" ^
  "$s.TargetPath = '%LAUNCH%rosarium.bat';" ^
  "$s.WorkingDirectory = '%LAUNCH%..';" ^
  "$s.Description = 'rosarium webmap';" ^
  "if (Test-Path '%ICON%') { $s.IconLocation = '%ICON%' };" ^
  "$s.Save();" ^
  "Write-Host ('  shortcut created: ' + $lnk);" ^
  "if (-not (Test-Path '%ICON%')) { Write-Host '  no launch\rosarium.ico: default icon used' }"
if errorlevel 1 echo   failed to create the shortcut
pause
