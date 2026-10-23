import sys

from kb.cli import app

if __name__ == "__main__":
    app(["eval-rag", *sys.argv[1:]])
