import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("count")
    args = parser.parse_args()
    print(int(args.count) * 2)

if __name__ == "__main__":
    main()
