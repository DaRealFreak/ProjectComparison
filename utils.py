import platform

def detect_platform():
    """
    Detects the current platform and returns the platform string for Chrome for Testing.
    Supported values: "linux64", "win64", "mac-x64", "mac-arm64".
    """
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows":
        return "win64"
    elif system == "Linux":
        return "linux64"
    elif system == "Darwin":
        return "mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64"
    else:
        raise Exception("Unsupported platform: " + system)
