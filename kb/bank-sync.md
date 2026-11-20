# Bank sync troubleshooting
Bank sync is available on Growth and Pro plans and supports FNB, Standard Bank, Absa, Nedbank, Capitec and Investec business accounts.
Transactions usually appear within 24 hours. If sync shows "Reconnect required", your bank has expired the connection (most banks require this every 90 days): go to Banking > Connections and click Reconnect.
If transactions are missing after reconnecting, click "Sync now" on the connection; Kettle imports up to 90 days of history. Duplicated transactions are usually caused by connecting the same account twice: delete the older connection.
