test
```bash
curl -G --data-urlencode 'q=гендир газпрома' 'http://127.0.0.1:8000/test'
```


see live logs
```bash
sudo journalctl -u leads-bot -f
```

see last 200 lines in logs
```bash
sudo journalctl -u leads-bot -n 200 --no-pager
```


reload
```bash
sudo systemctl restart leads-bot
```


install psql
```bash
sudo apt-get install -y postgresql-client-common postgresql-client
```


init dev db
```bash
./scripts/dev_db_test.sh
```

init prod db
```bash
DB_DSN="postgresql://postgres:PASSWORD@database-1.cdogwc8qwvgm.us-east-1.rds.amazonaws.com:5432/postgres" python3 -m src.migrate
```



