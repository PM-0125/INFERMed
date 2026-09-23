param(
    [Parameter(Mandatory = $true)]
    [string]$RemoteHost,
    [Parameter(Mandatory = $true)]
    [string]$RemoteUser,
    [string]$IdentityFile = "$env:USERPROFILE\.ssh\id_rsa.txt",
    [int]$LocalPort = 11435
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $IdentityFile -PathType Leaf)) {
    throw "SSH identity file is unavailable: $IdentityFile"
}
Write-Host "Forwarding 127.0.0.1:${LocalPort} to VM Ollama. Leave this terminal open; Ctrl+C stops the tunnel."
& ssh -N -T -o BatchMode=yes -o StrictHostKeyChecking=yes `
    -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 `
    -i $IdentityFile -L "127.0.0.1:${LocalPort}:127.0.0.1:11434" "${RemoteUser}@${RemoteHost}"
if ($LASTEXITCODE -ne 0) { throw "SSH tunnel exited with status $LASTEXITCODE" }
