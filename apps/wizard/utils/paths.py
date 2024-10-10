from etl.paths import BASE_DIR

WIZARD_CFG_NAME = ".wizardcfg"
WIZARD_DB_NAME = "wizard.db"

# PATH TO WIZARD CONFIG FOLDER
WIZARD_CFG = BASE_DIR / WIZARD_CFG_NAME

# PATH WIZARD DEFAULTS (old)
WIZARD_VARIABLES_DEFAULTS_OLD = BASE_DIR / ".wizard"

# PATH WIZARD DEFAULTS (new)
WIZARD_VARIABLES_DEFAULTS = WIZARD_CFG / "defaults.json"
WIZARD_DB = WIZARD_CFG / WIZARD_DB_NAME

# STREAMLIT SECRETS
STREAMLIT_SECRETS = BASE_DIR / ".streamlit" / "secrets.toml"
