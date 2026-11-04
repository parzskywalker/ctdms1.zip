<#
Builds the zero-install CTDMS pilot kit: its own Python + libraries + the app + the launchers, with a fresh, EMPTY
database (roles, settings and customer types only - no demo data).

  powershell -ExecutionPolicy Bypass -File deploy\pilot_kit\build_kit.ps1 [-Out 'D:\claude code\pilot_kit\CTDMS']

Needs the project's .venv (the tested libraries) and the Python it was made from. It never touches the project's
own data\ folder, and it refuses to overwrite a kit that already exists (it may hold a loaded database) unless -Force.
#>
param(
    [string]$Out = 'D:\claude code\pilot_kit\CTDMS',
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Tools = Join-Path $Repo 'deploy\pilot_kit'
$VenvPy = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPy)) { throw "No .venv in $Repo - run start_demo.bat once first." }
$PyHome = ((Get-Content (Join-Path $Repo '.venv\pyvenv.cfg')) | Where-Object { $_ -match '^home\s*=' }) -replace '^home\s*=\s*', ''
if (-not (Test-Path (Join-Path $PyHome 'python.exe'))) { throw "The Python the .venv was made from is gone: $PyHome" }

if (Test-Path $Out) {
    if (-not $Force) { throw "$Out already exists. It may hold a loaded database, so it is not overwritten. Use -Force for a throw-away build." }
    Remove-Item -LiteralPath $Out -Recurse -Force
}
New-Item -ItemType Directory -Path $Out -Force | Out-Null
$t0 = Get-Date

function Copy-Tree([string]$Src, [string]$Dst, [string[]]$SkipDirs = @()) {
    $rc = @($Src, $Dst, '/E', '/MT:16', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
    if ($SkipDirs.Count) { $rc += '/XD'; $rc += $SkipDirs }
    & robocopy @rc | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE): $Src" }
}

Write-Host '1/7 Python'
Copy-Tree $PyHome (Join-Path $Out 'python') @('Doc', 'include', 'libs', 'Scripts', '__pycache__', 'test', 'tests', 'idlelib', 'turtledemo', 'tkinter', 'ensurepip')
Write-Host '2/7 Libraries (from the tested .venv)'
Copy-Tree (Join-Path $Repo '.venv\Lib\site-packages') (Join-Path $Out 'site-packages') @('__pycache__', 'pip', 'setuptools', 'pytest', '_pytest', 'pluggy', 'iniconfig', '_distutils_hack')
Write-Host '3/7 The application, migrations and the Guided Training demonstration files'
foreach ($d in 'app', 'migrations') { Copy-Tree (Join-Path $Repo $d) (Join-Path $Out $d) @('__pycache__') }
Copy-Item (Join-Path $Repo 'alembic.ini') $Out
Copy-Tree (Join-Path $Repo 'docs\video_manual\v1.6\demo_files') (Join-Path $Out 'docs\video_manual\v1.6\demo_files')
Write-Host '4/7 Launchers'
foreach ($f in 'START_CTDMS.bat', 'STOP_CTDMS.bat', 'BACKUP_NOW.bat', 'SET_ADMIN_PASSWORD.bat', 'OPEN_FIREWALL_ONCE.bat',
               'CHECK_THIS_PC.bat', 'CHECK_THIS_PC.ps1', 'CHECK_COPY.bat', '_ctdms_env.bat', '_run_server.bat',
               'kit_tools.py', 'README_FIRST.txt') {
    Copy-Item (Join-Path $Tools $f) $Out
}
$ver = (& $VenvPy -c "from app import __version__; print(__version__)").Trim()
$commit = (& git -C $Repo rev-parse --short HEAD).Trim()
$dirty = if (& git -C $Repo status --porcelain -- app migrations deploy) { ' (+ uncommitted changes)' } else { '' }
"CTDMS $ver`r`nbuilt  $((Get-Date).ToString('dd-MMM-yyyy HH:mm'))`r`nsource $commit$dirty`r`npython $((& $VenvPy -c 'import sys; print(sys.version.split()[0])').Trim())" |
    Set-Content -Path (Join-Path $Out 'VERSION.txt') -Encoding ascii

# From here on nothing of this PC or of the project is used: only the kit, exactly as a clean PC would run it.
Get-ChildItem Env: | Where-Object { $_.Name -like 'CTDMS_*' } | ForEach-Object { Remove-Item "Env:$($_.Name)" }
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$env:PYTHONPATH = Join-Path $Out 'site-packages'
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONUTF8 = '1'
$env:CTDMS_ENVIRONMENT = 'production'
$env:CTDMS_SECRET_KEY = 'build-time-only-' + [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$env:CTDMS_SHOW_DEMO_LOGINS = 'false'
$py = Join-Path $Out 'python\python.exe'
Set-Location $Out

Write-Host '5/7 Microsoft runtime files'
& $py (Join-Path $Out 'kit_tools.py') runtime $Out --copy
if ($LASTEXITCODE -ne 0) { throw 'The kit is missing a Microsoft runtime file that this PC does not have either.' }
Write-Host '6/7 Pre-compiling (faster first start) and creating the empty database'
& $py -m compileall -q -j 0 --invalidation-mode checked-hash (Join-Path $Out 'python\Lib') (Join-Path $Out 'site-packages') (Join-Path $Out 'app') (Join-Path $Out 'migrations') | Out-Null
& $py -m app.cli init-db
if ($LASTEXITCODE -ne 0) { throw 'init-db failed inside the kit.' }
& $py -m app.cli pilot-verify
if ($LASTEXITCODE -ne 0) { throw 'The new database is not clean.' }
# The kit carries NO database. START_CTDMS.bat creates it, and a kit that is ever copied over a live C:\CTDMS must not be able to replace real records.
Remove-Item -LiteralPath (Join-Path $Out 'data') -Recurse -Force
Write-Host '7/7 File list with checksums'
& $py (Join-Path $Out 'kit_tools.py') manifest $Out

$mb = ((Get-ChildItem -LiteralPath $Out -Recurse -File -Force | Measure-Object Length -Sum).Sum) / 1MB
Write-Host ('Kit ready: {0}  ({1:N0} MB, {2:N0} s)' -f $Out, $mb, ((Get-Date) - $t0).TotalSeconds)
