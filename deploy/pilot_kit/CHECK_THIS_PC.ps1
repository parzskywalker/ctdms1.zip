# CTDMS pilot - read-only check of the host PC. Prints facts and a verdict; changes nothing.
$port = if ($env:CTDMS_PORT) { [int]$env:CTDMS_PORT } else { 8000 }
$ok = $true
function Line($label, $value, $good) {
    $mark = if ($good) { '  OK  ' } else { ' STOP ' }
    if (-not $good) { $script:ok = $false }
    '{0} {1,-16} {2}' -f $mark, $label, $value
}

''
'CTDMS - check of this PC (nothing is changed)'
'------------------------------------------------------------'
$os = Get-CimInstance Win32_OperatingSystem
$build = [int]$os.BuildNumber
Line 'Windows' ("{0}  (build {1})" -f $os.Caption, $build) ($build -ge 10240)
Line 'System type' $os.OSArchitecture ($os.OSArchitecture -like '64*')
Line 'Memory' ("{0:N1} GB" -f ($os.TotalVisibleMemorySize / 1MB)) (($os.TotalVisibleMemorySize / 1MB) -ge 3.5)
$c = Get-PSDrive -Name C -ErrorAction SilentlyContinue
if ($c) { Line 'Free space C:' ("{0:N0} GB" -f ($c.Free / 1GB)) (($c.Free / 1GB) -ge 2) }
Line 'Computer name' $env:COMPUTERNAME $true

$ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { $_.IPAddress })
Line 'Network address' ($(if ($ips) { ($ips -join ', ') } else { 'none - no network connection' })) ($ips.Count -gt 0)
foreach ($p in (Get-NetConnectionProfile -ErrorAction SilentlyContinue)) {
    Line 'Network type' ("{0} = {1}" -f $p.Name, $p.NetworkCategory) ($p.NetworkCategory -ne 'Public')
}
$rule = Get-NetFirewallRule -DisplayName 'CTDMS pilot' -ErrorAction SilentlyContinue
Line 'Firewall rule' ($(if ($rule) { 'CTDMS pilot is in place' } else { 'not yet - run OPEN_FIREWALL_ONCE.bat as administrator' })) ($null -ne $rule)
$listen = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
Line "Port $port" ($(if ($listen) { 'in use (CTDMS running, or another program)' } else { 'free' })) $true

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Line 'CTDMS folder' $here ($here -ieq 'C:\CTDMS')
Line 'Python files' ($(if (Test-Path (Join-Path $here 'python\python.exe')) { 'present' } else { 'MISSING - copy the whole folder again' })) (Test-Path (Join-Path $here 'python\python.exe'))
$kitPy = Join-Path $here 'python\python.exe'
$blocked = @(Get-NetFirewallApplicationFilter -Program $kitPy -ErrorAction SilentlyContinue | Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.Action -eq 'Block' -and $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' })
Line 'Windows block rule' ($(if ($blocked.Count) { 'YES, for this python.exe - run OPEN_FIREWALL_ONCE.bat as administrator' } else { 'none' })) ($blocked.Count -eq 0)
'------------------------------------------------------------'
if ($ok) { 'VERDICT: this PC is fit to host CTDMS.' } else { 'VERDICT: see the STOP lines above. (A STOP on "CTDMS folder" or "Firewall rule" only needs the setup steps.)' }
