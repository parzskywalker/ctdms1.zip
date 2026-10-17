param([string]$Json, [string]$OutDir)
Add-Type -AssemblyName System.Speech
$steps = Get-Content -Raw -Encoding UTF8 $Json | ConvertFrom-Json
New-Item -ItemType Directory -Force $OutDir | Out-Null
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice("Microsoft Zira Desktop")
$s.Rate = -1
foreach ($st in $steps) {
  $s.SetOutputToWaveFile((Join-Path $OutDir ($st.id + ".wav")))
  $s.Speak($st.text)
}
$s.SetOutputToNull()
$s.Dispose()
Write-Output ("voices: " + $steps.Count)
