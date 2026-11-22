"""Report mapping keys containing a colon without an equivalent standalone key.

The script reads team_mapping.json relative to the current working directory and
prints suggestions; it never changes the mapping.
"""

import json

def check_mapping():
    with open("team_mapping.json", "r", encoding="utf-8") as f:
        mapping = json.load(f)

    print("Keys with ':' where the team name isn't a standalone key:")
    print("-" * 60)
    
    count = 0
    for key, value in mapping.items():
        if ":" in key:
            # Extract the team name after the colon
            team_name = key.split(":", 1)[1].strip()
            
            # Check if this stripped team name exists as a key on its own
            if team_name not in mapping:
                print(f'"{key}": "{value}"')
                print(f'  -> "{team_name}" is missing as its own key!\n')
                count += 1
                
    if count == 0:
        print("All teams have a standalone entry.")

if __name__ == "__main__":
    check_mapping()
