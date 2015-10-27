import sys, os, threading, traceback, time
import psycopg2
import Config, Logger, Blocknotify
from bitcoinrpc.authproxy import AuthServiceProxy, JSONRPCException
from decimal import Decimal

def database():
	return psycopg2.connect(database = Config.config["database"])

class daemonClass(object):
	def __init__(self, coinuri = None):
		self.coinuri = Config.config["coinuri"] if coinuri is None else coinuri
		self.rpc = AuthServiceProxy(self.coinuri)

	def __call__(self):
		return self.rpc

cur = database().cursor()
cur.execute("SELECT block FROM lastblock")
lastblock = cur.fetchone()[0]
del cur
daemon = daemonClass()

class NotEnoughMoney(Exception):
	pass
class InsufficientFunds(Exception):
	pass

unconfirmed = {}

def txlog(cursor, token, amt, tx = None, address = None, src = None, dest = None):
	cursor.execute("INSERT INTO txlog VALUES (%s, %s, %s, %s, %s, %s, %s)", (time.time(), token, src, dest, amt, tx, address))

def notify_block(): 
	global lastblock, unconfirmed
	lb = daemon().listsinceblock(lastblock, Config.config["confirmations"])
	db = database()
	cur = db.cursor()
	txlist = [(int(tx['amount']), tx['address']) for tx in lb["transactions"] if tx['category'] == "receive" and tx['confirmations'] >= Config.config["confirmations"]]
	if len(txlist):
		addrlist = [(tx[1],) for tx in txlist]
		cur.executemany("UPDATE accounts SET balance = balance + %s FROM address_account WHERE accounts.account = address_account.account AND address_account.address = %s", txlist)
		cur.executemany("UPDATE address_account SET used = '1' WHERE address = %s", addrlist)
	unconfirmed = {}
	for tx in lb["transactions"]:
		if tx['category'] == "receive":
			cur.execute("SELECT account FROM address_account WHERE address = %s", (tx['address'],))
			if cur.rowcount:
				account = cur.fetchone()[0]
				if tx['confirmations'] < Config.config["confirmations"]:
						unconfirmed[account] = unconfirmed.get(account, 0) + int(tx['amount'])
				else:
					txlog(cur, Logger.token(), int(tx['amount']), tx = tx['txid'].encode("ascii"), address = tx['address'], dest = account)
	cur.execute("UPDATE lastblock SET block = %s", (lb["lastblock"],))
	db.commit()
	lastblock = lb["lastblock"]

def balance(account): 
	cur = database().cursor()
	cur.execute("SELECT balance FROM accounts WHERE account = %s", (account,))
	if cur.rowcount:
		return cur.fetchone()[0]
	else:
		return 0

def balance_unconfirmed(account):
	return unconfirmed.get(account, 0)

def tip(token, source, target, amount): 
	db = database()
	cur = db.cursor()
	cur.execute("SELECT * FROM accounts WHERE account = ANY(%s) FOR UPDATE", (sorted([target, source]),))
	try:
		cur.execute("UPDATE accounts SET balance = balance - %s WHERE account = %s", (amount, source))
	except psycopg2.IntegrityError as e:
		raise NotEnoughMoney()
	if not cur.rowcount:
		raise NotEnoughMoney()
	cur.execute("UPDATE accounts SET balance = balance + %s WHERE account = %s", (amount, target)) 
	if not cur.rowcount:
		cur.execute("INSERT INTO accounts VALUES (%s, %s)", (target, amount))
	txlog(cur, token, amount, src = source, dest = target)
	db.commit()

def tip_multiple(token, source, dict):
	db = database()
	cur = db.cursor()
	cur.execute("SELECT * FROM accounts WHERE account = ANY(%s) FOR UPDATE", (sorted(dict.keys() + [source]),))
	spent = 0
	for target in dict:
		amount = dict[target]
		try:
			cur.execute("UPDATE accounts SET balance = balance - %s WHERE account = %s", (amount, source))
		except psycopg2.IntegrityError as e:
			raise NotEnoughMoney()
		if not cur.rowcount:
			raise NotEnoughMoney()
		spent += amount
		cur.execute("UPDATE accounts SET balance = balance + %s WHERE account = %s", (amount, target)) 
		if not cur.rowcount:
			cur.execute("INSERT INTO accounts VALUES (%s, %s)", (target, amount))
	for target in dict:
		txlog(cur, token, dict[target], src = source, dest = target)
	db.commit()

def withdraw(token, account, address, amount): 
	db = database()
	cur = db.cursor()
	try:
		cur.execute("UPDATE accounts SET balance = balance - %s WHERE account = %s", (amount + 1, account))
	except psycopg2.IntegrityError as e:
		raise NotEnoughMoney()
	if not cur.rowcount:
		raise NotEnoughMoney()
	try:
		tx = daemon().sendtoaddress(address, amount, "sent with %s" % Config.config['account'])
	except JSONRPCException as e:
		if e.code != -4:
			Logger.log("c", "Unknown error with sendtoaddress: %s" % e)
		raise InsufficientFunds()
	except:
		Logger.irclog("Emergency lock on account '%s'" % (account))
		lock(account, True)
		raise
	db.commit()
	txlog(cur, token, amount + 1, tx = tx.encode("ascii"), address = address, src = account)
	db.commit()
	return tx.encode("ascii")

def deposit_address(account): 
	db = database()
	cur = db.cursor()
	cur.execute("SELECT address FROM address_account WHERE used = '0' AND account = %s LIMIT 1", (account,))
	if cur.rowcount:
		return cur.fetchone()[0]
	addr = daemon().getnewaddress()
	try:
		cur.execute("SELECT * FROM accounts WHERE account = %s", (account,))
		if not cur.rowcount:
			cur.execute("INSERT INTO accounts VALUES (%s, 0)", (account,))
		cur.execute("INSERT INTO address_account VALUES (%s, %s, '0')", (addr, account))
		db.commit()
	except:
		pass
	return addr.encode("ascii")

def verify_address(address):
	if address.isalnum():
		return daemon().validateaddress(address)["isvalid"]
	else:
		return False

def ping():
	daemon().getbalance()

def balances():
	cur = database().cursor()
	cur.execute("SELECT SUM(balance) FROM accounts")
	db = Decimal(cur.fetchone()[0])
	dogecoind = Decimal(daemon().getbalance('', Config.config["confirmations"]))
	return (db, dogecoind)

def get_info():
	info = daemon().getinfo()
	return (info, daemon().getblockhash(info['blocks']).encode("ascii"))

def lock(account, state = None):
	if state == None:
		cur = database().cursor()
		cur.execute("SELECT * FROM locked WHERE account = %s", (account,))
		return not not cur.rowcount
	elif state == True:
		db = database()
		cur = db.cursor()
		try:
			cur.execute("INSERT INTO locked VALUES (%s)", (account,))
			db.commit()
		except psycopg2.IntegrityError as e:
			pass
	elif state == False:
		db = database()
		cur = db.cursor()
		cur.execute("DELETE FROM locked WHERE account = %s", (account,))
		db.commit()
