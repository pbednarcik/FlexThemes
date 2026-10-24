[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Artifact,
    [Parameter(Mandatory)][string]$VerifierJar,
    [string]$ReportDirectory = 'build/release-prep/plugin-verifier',
    # A JDK 21 java; defaults to JAVA_HOME, then java on PATH.
    [string]$Java = $(if ($env:JAVA_HOME) { Join-Path $env:JAVA_HOME 'bin/java.exe' } else { 'java' }),
    # Folder holding the four IDE installs; the default is where the Windows per-user installers put them.
    [string]$IdeRoot = (Join-Path $env:LOCALAPPDATA 'Programs')
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$report = [IO.Path]::GetFullPath((Join-Path $repo $ReportDirectory))
$ownedBuild = [IO.Path]::GetFullPath((Join-Path $repo 'build')) + [IO.Path]::DirectorySeparatorChar
if (-not $report.StartsWith($ownedBuild, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Verifier reports must stay inside this repository build directory.'
}
if (Test-Path -LiteralPath $report) { throw 'Choose a fresh verifier report directory; old verdicts must not count.' }
$artifactPath = (Resolve-Path -LiteralPath $Artifact).Path
$verifierPath = (Resolve-Path -LiteralPath $VerifierJar).Path
New-Item -ItemType Directory -Path $report -Force | Out-Null
$homes = @('Rider', 'IntelliJ IDEA Ultimate', 'GoLand', 'PyCharm') | ForEach-Object {
    $ideHome = Join-Path $IdeRoot $_
    if (-not (Test-Path -LiteralPath (Join-Path $ideHome 'product-info.json'))) { throw "No IDE at '$ideHome'. Pass -IdeRoot." }
    $ideHome
}
$verdictDirectory = Join-Path $report 'results'
# The verifier clears its reports directory before starting. Keep the open
# launcher log and invocation receipt outside that child directory on Windows.
$arguments = @(
    "-Dplugin.verifier.home.dir=$repo/build/release-prep/verifier-home",
    '-jar', $verifierPath, 'check-plugin', $artifactPath
) + $homes + @('-verification-reports-dir', $verdictDirectory, '-offline')
$receipt = [ordered]@{
    artifact = $artifactPath
    artifactSha256 = (Get-FileHash -LiteralPath $artifactPath).Hash.ToLowerInvariant()
    verifier = $verifierPath
    verifierSha256 = (Get-FileHash -LiteralPath $verifierPath).Hash.ToLowerInvariant()
    command = @($Java) + $arguments
}
$receipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $report 'invocation.json') -Encoding utf8NoBOM
& $Java @arguments *> (Join-Path $report 'launcher.log')
$code = $LASTEXITCODE
$receipt['exitCode'] = $code
$receipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $report 'invocation.json') -Encoding utf8NoBOM
if ($code -ne 0) { throw "Plugin Verifier exited $code; inspect $report" }
$verdicts = @(Get-ChildItem -LiteralPath $report -Recurse -File -Filter 'verification-verdict.txt')
if ($verdicts.Count -ne 4) { throw 'Expected exactly four native IDE verdicts.' }
foreach ($verdict in $verdicts) {
    if ((Get-Content -LiteralPath $verdict.FullName -Raw).Trim() -ne 'Compatible') {
        throw "Plugin Verifier reported a non-compatible result: $($verdict.FullName)"
    }
}
foreach ($name in @('compatibility-problems.txt', 'compatibility-warnings.txt', 'invalid-plugin.txt', 'plugin-structure-warnings.txt')) {
    $problems = @(Get-ChildItem -LiteralPath $report -Recurse -File -Filter $name | Where-Object Length -gt 0)
    if ($problems.Count) { throw "Verifier reported $name; inspect $report" }
}
Write-Output "Plugin Verifier: Compatible in all four IDEs. Reports: $report"
