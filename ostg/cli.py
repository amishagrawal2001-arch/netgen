def main():
    # Try common entry names in your client script
    try:
        from run_tgen_client import main as _main
    except Exception:
        # Fallback: execute the module (works if it has if __name__ == "__main__")
        import run_tgen_client as _m  # noqa: F401 (import triggers side effects)
        return
    _main()
