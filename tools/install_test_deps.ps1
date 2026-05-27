param(
    [string]$VenvPath = ".\.venv",
    [string]$WheelDir = ".\wheels",
    [string[]]$Packages = @("pytest", "Pillow", "psutil", "fastapi", "pydantic"),
    [switch]$ForceOffline
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if ((Get-Location).Path -ieq "$env:WINDIR\System32") {
    throw "Run this script from the repository root, not C:\Windows\System32."
}

$TrustedHosts = @(
    "--trusted-host", "pypi.org",
    "--trusted-host", "files.pythonhosted.org",
    "--trusted-host", "pypi.python.org"
)

function Find-Proxy {
    if ($env:HTTPS_PROXY) {
        return $env:HTTPS_PROXY
    }
    try {
        $settings = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -ErrorAction Stop
        if ($settings.ProxyEnable -eq 1 -and $settings.ProxyServer) {
            if ($settings.ProxyServer -match "^https?://") {
                return $settings.ProxyServer
            }
            return "http://$($settings.ProxyServer)"
        }
    } catch {
        return $null
    }
    return $null
}

function Find-Python {
    foreach ($candidate in @("py", "python", "python3")) {
        if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) {
            continue
        }
        if ($candidate -eq "py") {
            & py -3 --version *> $null
            if ($LASTEXITCODE -eq 0) {
                return @("py", "-3")
            }
        } else {
            & $candidate --version *> $null
            if ($LASTEXITCODE -eq 0) {
                return @($candidate)
            }
        }
    }
    throw "No Python 3 in PATH."
}

function Invoke-Pip {
    param(
        [string]$Label,
        [string[]]$Arguments
    )
    Write-Host "`n>>> $Label" -ForegroundColor Green
    & $script:PythonExe -m pip @Arguments
    return $LASTEXITCODE
}

function Get-CompatibleWheel {
    param($Metadata, [string]$PyTag)

    $wheels = @($Metadata.urls | Where-Object { $_.packagetype -eq "bdist_wheel" })
    $compatible = $wheels | Where-Object {
        $name = $_.filename.ToLowerInvariant()
        (
            $name -match "py3-none-any\.whl$" -or
            $name -match "$PyTag-[^-]+-win_amd64\.whl$" -or
            $name -match "$PyTag-[^-]+-win32\.whl$" -or
            $name -match "$PyTag-[^-]+-win_arm64\.whl$"
        ) -and
        $name -notmatch "macosx|manylinux|musllinux|ios_|android"
    }
    return $compatible | Select-Object -First 1
}

$proxy = Find-Proxy
if ($proxy) {
    Write-Host "Proxy: $proxy" -ForegroundColor Yellow
    $env:HTTP_PROXY = $proxy
    $env:HTTPS_PROXY = $proxy
}

$bootstrap = Find-Python
Write-Host "Bootstrap: $($bootstrap -join ' ')" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $VenvPath)) {
    & $bootstrap[0] @($bootstrap | Select-Object -Skip 1) -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) {
        throw "venv creation failed."
    }
}

$script:PythonExe = (Resolve-Path (Join-Path $VenvPath "Scripts\python.exe")).Path
Write-Host "Target: $script:PythonExe" -ForegroundColor Cyan

$common = @() + $TrustedHosts
if ($proxy) {
    $common += @("--proxy", $proxy)
}

$rc = Invoke-Pip "Upgrading pip toolchain" (@("install", "--upgrade", "pip", "setuptools", "wheel") + $common)
if ($rc -ne 0) {
    throw "Failed to upgrade pip toolchain."
}

if (-not $ForceOffline) {
    $rc = Invoke-Pip "Installing test dependencies" (@("install") + $common + $Packages)
    if ($rc -eq 0) {
        & $script:PythonExe -c "import pytest, PIL, psutil, fastapi, pydantic; print('imports ok')"
        exit $LASTEXITCODE
    }

    $rc = Invoke-Pip "Installing test dependencies, wheels only" (@("install", "--only-binary=:all:", "--prefer-binary") + $common + $Packages)
    if ($rc -eq 0) {
        & $script:PythonExe -c "import pytest, PIL, psutil, fastapi, pydantic; print('imports ok')"
        exit $LASTEXITCODE
    }
}

New-Item -ItemType Directory -Force -Path $WheelDir | Out-Null
$pyTag = (& $script:PythonExe -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')").Trim()
Write-Host "Python wheel tag: $pyTag" -ForegroundColor Cyan

foreach ($pkg in $Packages) {
    $metadataArgs = @{ Uri = "https://pypi.org/pypi/$pkg/json"; UseBasicParsing = $true }
    if ($proxy) {
        $metadataArgs.Proxy = $proxy
    }
    $metadata = Invoke-RestMethod @metadataArgs
    $wheel = Get-CompatibleWheel -Metadata $metadata -PyTag $pyTag
    if (-not $wheel) {
        throw "No compatible Windows wheel found for $pkg and $pyTag."
    }
    $out = Join-Path $WheelDir $wheel.filename
    Write-Host "  pulling $($wheel.filename)" -ForegroundColor Gray
    $downloadArgs = @{ Uri = $wheel.url; OutFile = $out; UseBasicParsing = $true }
    if ($proxy) {
        $downloadArgs.Proxy = $proxy
    }
    Invoke-WebRequest @downloadArgs
}

$rc = Invoke-Pip "Offline install from wheel cache" (@("install", "--no-index", "--find-links", $WheelDir) + $Packages)
if ($rc -ne 0) {
    throw "Offline install failed. Ensure the wheel cache includes transitive dependencies, or allow pip network access."
}

& $script:PythonExe -c "import pytest, PIL, psutil, fastapi, pydantic; print('imports ok')"
if ($LASTEXITCODE -ne 0) {
    throw "Validation failed."
}

Write-Host "`nAll packages installed.`nActivate:  . $VenvPath\Scripts\Activate.ps1" -ForegroundColor Green
