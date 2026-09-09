<#
Restores ProjectO's locally-cloned third-party research repositories.
The repositories stay ignored by ProjectO Git; see third_party/README.md for
licensing and integration boundaries.
#>

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $projectRoot "third_party"

$repositories = @(
    @{ Name = "smartapi-python"; Url = "https://github.com/angel-one/smartapi-python.git" },
    @{ Name = "bhav"; Url = "https://github.com/rajmaurya0904/bhav.git" },
    @{ Name = "options-straddle-dashboard"; Url = "https://github.com/p1-patidar/options-straddle-dashboard.git" },
    @{ Name = "ui-trading-system"; Url = "https://github.com/Raahi-Bhushan/ui-trading-system.git" },
    @{ Name = "options-day-trader-agent"; Url = "https://github.com/bhavesh0009/options-day-trader-agent.git" },
    @{ Name = "awesome-algo-trading-india"; Url = "https://github.com/tradevectorsrobots/awesome-algo-trading-india.git" }
)

New-Item -ItemType Directory -Path $destination -Force | Out-Null

foreach ($repository in $repositories) {
    $path = Join-Path $destination $repository.Name
    if (Test-Path (Join-Path $path ".git")) {
        Write-Host "Already present: $($repository.Name)"
        continue
    }

    Write-Host "Cloning: $($repository.Name)"
    git clone --depth 1 $repository.Url $path
}

Write-Host "Third-party catalogue is ready. Review third_party/README.md before reuse."
