# Copies the newest CTDMS backups from the cloud server to this Windows PC (Kolkata server, a head-office PC or the
# Guma master PC), so a copy always lives outside the cloud. Uses the SSH client built into Windows 10/11.
#
# One-time set-up (done by the person who owns the server key; no password is stored here):
#   1. Put the server's SSH key file on this PC, readable only by you, e.g. C:\CTDMS_backup\server_key.pem
#   2. Test once by hand:  powershell -ExecutionPolicy Bypass -File PULL_BACKUP.ps1 -Server ubuntu@ctdms.example.in -Key C:\CTDMS_backup\server_key.pem
#   3. Run it every hour with Windows Task Scheduler (same command; "Run whether user is logged on or not").
#
# shortcut: copies are protected by the SSH transfer and this PC's own login, not encrypted at rest; add encryption
# if the copies ever sit on a machine other people administer.
param(
    [Parameter(Mandatory = $true)][string]$Server,      # user@host of the cloud server
    [Parameter(Mandatory = $true)][string]$Key,         # path of the SSH private key file
    [string]$RemoteDir = "/opt/ctdms/backups",
    [string]$LocalDir = "C:\CTDMS_backup\copies",
    [int]$KeepDays = 30
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $LocalDir | Out-Null
$log = Join-Path $LocalDir "pull_log.txt"
try {
    # Newest database dump and newest uploads archive on the server.
    $names = ssh -i $Key -o BatchMode=yes -o StrictHostKeyChecking=accept-new $Server `
        "cd '$RemoteDir' && ls -1t ctdms-*.dump | head -1 && (ls -1t ctdms-uploads-*.tar.gz 2>/dev/null | head -1 || true)"
    if ($LASTEXITCODE -ne 0 -or -not $names) { throw "could not list $RemoteDir on $Server" }
    foreach ($n in @($names)) {
        if (-not $n) { continue }
        $dest = Join-Path $LocalDir $n
        if (Test-Path $dest) { continue }   # already copied
        scp -i $Key -o BatchMode=yes "${Server}:$RemoteDir/$n" $dest
        if ($LASTEXITCODE -ne 0) { throw "copy of $n failed" }
        if ((Get-Item $dest).Length -eq 0) { Remove-Item $dest; throw "copy of $n is empty" }
    }
    Get-ChildItem $LocalDir -Filter "ctdms-*" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) } | Remove-Item
    Add-Content $log "$(Get-Date -Format s) OK $(@($names) -join ', ')"
} catch {
    Add-Content $log "$(Get-Date -Format s) FAILED $($_.Exception.Message)"
    exit 1
}
