import json
import os
from typing import Dict, Any, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
ENV_FILE = os.path.join(BASE_DIR, '.env')

PROVIDER_ENV_MAP = {
    "DeepSeek": "DEEPSEEK_API_KEY",
    "OpenAI": "OPENAI_API_KEY",
    "阿里云通义千问 (Qwen)": "DASHSCOPE_API_KEY",
    "硅基流动 (SiliconFlow)": "SILICONFLOW_API_KEY",
    "自定义 (Custom OpenAI-Compatible)": "CUSTOM_LLM_API_KEY"
}

def parse_env_file() -> Dict[str, str]:
    """Parse local .env file if it exists."""
    env_vars = {}
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        env_vars[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
    return env_vars

def write_env_key(key_name: str, key_val: str) -> None:
    """Save secret key to local git-ignored .env file."""
    current_env = parse_env_file()
    current_env[key_name] = key_val
    try:
        with open(ENV_FILE, 'w', encoding='utf-8') as f:
            f.write("# Local private environment variables (NEVER COMMIT)\n")
            for k, v in current_env.items():
                f.write(f"{k}={v}\n")
    except Exception as e:
        print(f"Error writing to .env: {e}")

def load_config() -> Dict[str, Any]:
    """Load non-sensitive configuration from config.json."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        return {}

def save_config(config_data: Dict[str, Any]) -> bool:
    """Save non-sensitive configuration to config.json (stripping any accidental keys)."""
    try:
        current = load_config()
        # Security sanity check: never persist raw keys into config.json
        clean_data = {k: v for k, v in config_data.items() if "key" not in k.lower()}
        current.update(clean_data)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Error saving config: {e}")
        return False

def get_provider_key(provider: str) -> str:
    """
    Get API key for provider.
    Priority:
    1. System Environment Variable (e.g. DEEPSEEK_API_KEY)
    2. Local .env file (gitignored)
    """
    env_var_name = PROVIDER_ENV_MAP.get(provider, "")
    if env_var_name:
        val = os.getenv(env_var_name)
        if val:
            return val
    env_vars = parse_env_file()
    return env_vars.get(env_var_name, "")

def set_provider_key(provider: str, key: str) -> None:
    """Persist API key securely to .env file (never config.json)."""
    env_var_name = PROVIDER_ENV_MAP.get(provider, "CUSTOM_LLM_API_KEY")
    os.environ[env_var_name] = key
    write_env_key(env_var_name, key)

def get_firecrawl_key() -> str:
    """Get Firecrawl API key from environment or .env."""
    val = os.getenv("FIRECRAWL_API_KEY")
    if val:
        return val
    env_vars = parse_env_file()
    return env_vars.get("FIRECRAWL_API_KEY", "")

def set_firecrawl_key(key: str) -> None:
    """Persist Firecrawl API key securely to .env."""
    os.environ["FIRECRAWL_API_KEY"] = key
    write_env_key("FIRECRAWL_API_KEY", key)

def clear_all_credentials() -> bool:
    """Clear all saved credentials in .env."""
    if os.path.exists(ENV_FILE):
        try:
            os.remove(ENV_FILE)
        except Exception:
            return False
    # Clear memory env
    for k in list(PROVIDER_ENV_MAP.values()) + ["FIRECRAWL_API_KEY"]:
        if k in os.environ:
            del os.environ[k]
    return True
