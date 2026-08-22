param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$OpenCodeArgs
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"

if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        $parts = $trimmed.Split("=", 2)
        if ($parts.Count -eq 2) {
            [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
        }
    }
}

if (-not $env:POGO_MCP_URL) {
    $env:POGO_MCP_URL = "http://127.0.0.1:8090/mcp"
}

# OPENCODE_CONFIG_CONTENT has higher precedence than the project config. This
# selects the model that is actually installed without duplicating the safety
# policy and MCP definitions kept in opencode.jsonc and AGENTS.md.
$env:OPENCODE_CONFIG_CONTENT = @'
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama/qwen3.8:27b",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local only)",
      "options": {
        "baseURL": "http://127.0.0.1:11434/v1"
      },
      "models": {
        "qwen3.8:27b": {
          "name": "Qwen 3.8 27B (local)",
          "limit": {
            "context": 16384,
            "output": 4096
          }
        }
      }
    }
  }
}
'@

Push-Location $projectRoot
try {
    if ($OpenCodeArgs.Count -gt 0) {
        & opencode @OpenCodeArgs
    }
    else {
        & opencode
    }
}
finally {
    Pop-Location
}
