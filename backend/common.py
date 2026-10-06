import csv, os
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
COLS = ['id', 'author', 'parent_id', 'root_id', 'body', 'created_utc', 'followers', 'following', 'statuses', 'acct_created', 'is_bot']

def save(name, rows):
    with open(f'{D}/{name}.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(COLS[:len(rows[0])])
        w.writerows(rows)
    print(f'saved {len(rows)} rows -> data/{name}.csv')
