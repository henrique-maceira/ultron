# Atualiza o Ultron na máquina (Windows / PowerShell).
# Puxa a branch de desenvolvimento do GitHub e, SÓ se houver mudanças, reconstrói
# e reinicia o container. Feito para ser chamado manualmente ou pelo Agendador de Tarefas.
$ErrorActionPreference = "Stop"

$Branch = "claude/virtual-task-assistant-mdop7k"

# Vai para a raiz do projeto (pasta pai deste script).
Set-Location -Path (Join-Path $PSScriptRoot "..")

Write-Host "[Ultron] Buscando atualizações em '$Branch'..."
git fetch origin | Out-Null
git checkout $Branch | Out-Null

$before = (git rev-parse HEAD).Trim()
git pull --ff-only origin $Branch
$after = (git rev-parse HEAD).Trim()

if ($before -ne $after) {
    Write-Host "[Ultron] Novidades detectadas ($before -> $after). Reconstruindo container..."
    docker compose up -d --build
    docker image prune -f | Out-Null
    Write-Host "[Ultron] Atualizado e no ar."
} else {
    Write-Host "[Ultron] Já está na versão mais recente. Nada a fazer."
}
