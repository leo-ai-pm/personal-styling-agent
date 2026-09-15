"""Start the extended, synthetic demo. Configure the model in .env first."""
import sys
from m2.server import main

if __name__ == "__main__":
    if "--preview-assets" not in sys.argv:
        sys.argv.append("--preview-assets")
    main()
