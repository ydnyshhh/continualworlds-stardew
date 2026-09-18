param(
    [string]$GamePath = 'E:\GAMES\Stardew-Valley-AnkerGames\Stardew Valley',
    [ValidateRange(1024, 65535)][int]$Port = 10783
)
$ErrorActionPreference = 'Stop'
$executable = Join-Path $GamePath 'StardewModdingAPI.exe'
if (-not (Test-Path -LiteralPath $executable)) { throw "SMAPI not found: $executable" }
$probe = [Net.Sockets.TcpClient]::new()
try {
    $probe.Connect('127.0.0.1', $Port)
    throw "Port $Port is occupied. Close the existing experiment or choose another port."
} catch [Net.Sockets.SocketException] {
    # No existing listener. Never terminate another process to free the port.
} finally {
    $probe.Dispose()
}
Push-Location -LiteralPath $GamePath
try {
    & $executable --port-id $Port
} finally {
    Pop-Location
}
