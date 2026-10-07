"""Script para probar el manejo de errores del servidor MCP."""
import subprocess
import json

# Ejecutar el servidor y enviar una llamada inválida
resultado = subprocess.run(
    ["python", "-m", "uv", "run", "mcp", "dev", "server.py"],
    input=json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "add",
            "arguments": {"a": "dos", "b": 3}
        }
    }),
    capture_output=True,
    text=True,
    timeout=5
)

print("Respuesta del servidor:")
print(resultado.stdout)
print("\nErrores (si los hay):")
print(resultado.stderr)