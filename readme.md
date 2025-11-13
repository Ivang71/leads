test
```bash
curl -G --data-urlencode 'q=гендир газпрома' 'http://127.0.0.1:8000/test'
```


check logs
```bash
sudo journalctl -u leads-bot -f
```

```bash
sudo journalctl -u leads-bot -n 200 --no-pager
```


reload
```bash
sudo systemctl restart leads-bot
```





