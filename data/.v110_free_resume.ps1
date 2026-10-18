$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $root '.venv\Scripts\python.exe'
$stateLog = Join-Path $PSScriptRoot '.v110_free_resume.log'
$timezone = [System.TimeZoneInfo]::FindSystemTimeZoneById('Pacific Standard Time')
$retryCount = 0

while ($true) {
    $stamp = [DateTime]::Now.ToString('yyyyMMdd-HHmmss')
    $stdoutPath = Join-Path $PSScriptRoot ".v110_resume_$stamp.out.log"
    $stderrPath = Join-Path $PSScriptRoot ".v110_resume_$stamp.err.log"
    $process = Start-Process -FilePath $python `
        -ArgumentList @('-u', 'scripts/enrich_v110_character_fields.py', '--apply', '--project-id', 'knowley-1', '--workers', '8') `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    $process.WaitForExit()

    $stdout = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -Raw -LiteralPath $stdoutPath } else { '' }
    $stderr = if (Test-Path -LiteralPath $stderrPath) { Get-Content -Raw -LiteralPath $stderrPath } else { '' }
    $output = "$stdout`n$stderr"
    Add-Content -LiteralPath $stateLog -Value "[$stamp] exit=$($process.ExitCode)"

    if ($output -match 'No unfinished semantic fields found\.') {
        Add-Content -LiteralPath $stateLog -Value 'All records are complete.'
        break
    }

    if ($output -match 'Gemini free quota reached; stopping cleanly') {
        Add-Content -LiteralPath $stateLog -Value 'Free Gemini quota reached; waiting for the next Pacific midnight reset.'
        $nowUtc = [DateTime]::UtcNow
        $pacificNow = [System.TimeZoneInfo]::ConvertTimeFromUtc($nowUtc, $timezone)
        $nextMidnightPacific = $pacificNow.Date.AddDays(1)
        $nextMidnightUtc = [System.TimeZoneInfo]::ConvertTimeToUtc($nextMidnightPacific, $timezone)
        while ([DateTime]::UtcNow -lt $nextMidnightUtc) {
            $secondsLeft = ($nextMidnightUtc - [DateTime]::UtcNow).TotalSeconds
            Start-Sleep -Seconds ([Math]::Min(60, [Math]::Max(1, [int][Math]::Ceiling($secondsLeft))))
        }
        continue
    }

    if ($process.ExitCode -eq 0) {
        Add-Content -LiteralPath $stateLog -Value 'Enrichment finished without a quota stop.'
        break
    }

    $retryCount += 1
    if ($retryCount -ge 5) {
        Add-Content -LiteralPath $stateLog -Value 'Stopped after five non-quota failures; manual review is needed.'
        break
    }
    Add-Content -LiteralPath $stateLog -Value "Non-quota failure $retryCount/5; retrying after five minutes."
    Start-Sleep -Seconds 300
}
