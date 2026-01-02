# main.py
from rss_collector import main as run_collector

def run_all():
    print("=== Starting Geopolitical Collector ===")
    run_collector()
    print("=== Completed ===")

if __name__ == "__main__":
    run_all()
