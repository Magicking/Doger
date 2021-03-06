# coding=utf8
import sys, os, time, math, pprint
import Irc, Transactions, Blocknotify, Logger, Global, Hooks, Config
import decimal
from CoinConfig import coinconfig

commands = {}

def ping(req, _):
	"""%ping - Pong"""
	req.reply("Pong")
commands["ping"] = ping

def balance(req, _):
	"""%balance - Displays your confirmed and unconfirmed balance"""
	acct = Irc.account_names([req.nick])[0]
	if not acct:
		return req.reply_private("You are not identified with freenode services (see /msg NickServ help)")
	confirmed = Transactions.balance(acct)
	pending = Transactions.balance_unconfirmed(acct)
	if pending:
		req.reply(coinconfig["balance_confirm"] % (confirmed, pending))
	else:
		req.reply(coinconfig["balance"] % confirmed)
commands["balance"] = balance

def deposit(req, _):
	"""%deposit - Displays your deposit address"""
	acct = Irc.account_names([req.nick])[0]
	if not acct:
		return req.reply_private("You are not identified with freenode services (see /msg NickServ help)")
	req.reply_private("To deposit, send coins to %s (transactions will be credited after %d confirmations)" % (Transactions.deposit_address(acct), Config.config["confirmations"]))
commands["deposit"] = deposit

def parse_amount(s, acct, all_offset = 0, out_type = int):
	if s.lower() == "all":
		return max(Transactions.balance(acct) + all_offset, 1)
	else:
		try:
			amount = decimal.Decimal(s)
			if amount.is_nan():
				raise ValueError
		except (ValueError, decimal.InvalidOperation):
			raise ValueError(repr(s) + " - invalid amount")
		if amount > 1e12:
			raise ValueError(repr(s) + " - invalid amount (value too large)")
		if amount <= 0:
			raise ValueError(repr(s) + " - invalid amount (should be 1 or more)")
		if not out_type(amount) == amount:
			raise ValueError(repr(s) + " - invalid amount (should be %s)" % out_type.__name__)
		return out_type(amount)

def giftcard(req, arg):
	"""%giftcard [number] [price] [card_price] - Give you 'number' Amazon Gift Card of $[card_price] for [price] NEU. If no number specified, number equal 1, if no card_price specified, card_price equal $0.50"""
	if len(arg) > 3:
		return req.reply(gethelp("giftcard"))
	acct = Irc.account_names([req.nick])[0]
	if not acct:
		return req.reply_private("You are not identified with freenode services (see /msg NickServ help)")
	if Transactions.lock(acct):
		return req.reply_private("Your account is currently locked")
	number = Config.config["number"]
	price = Config.config['price']
	card_price = parse_amount(Config.config['card_price'], None, 0, decimal.Decimal)
	if len(arg) >= 1:
		try:
			if arg[0] == 'all':
				return req.reply_private('Not implemented yet.')
			number = parse_amount(arg[0], acct)
		except ValueError as e:
			return req.reply_private(str(e))
	if len(arg) >= 2:
		try:
			if arg[1] == 'all':
				return req.reply_private('%s - invalid amount (should be integer)' % arg[1])
			price = parse_amount(arg[1], acct)
		except ValueError as e:
			return req.reply_private(str(e))
	if len(arg) >= 3:
		try:
			if arg[2] == 'all':
				return req.reply_private('%s - invalid amount (should be integer)' % arg[2])
			card_price_val = parse_amount(arg[2], acct, 0, decimal.Decimal)
			if card_price_val < card_price:
				return req.reply_private('Card price below limit of %s' % card_price)
			card_price = card_price_val
		except ValueError as e:
			return req.reply_private(str(e))
	token = Logger.token()
	try:
		gifts_cards = Transactions.giftcard(token, acct, number, price, card_price * 100)
		req.reply_private('[%s] Neucoin total: %d %s (%s %s), Card total: $%s ($%s)' % (
		token, price*number, coinconfig['unit'], price, coinconfig['unit'],
		card_price*number, card_price))
		txt = ''
		for code in gifts_cards:
			txt += 'AMZN: %s, ' % code
		txt = txt[:-2]
		req.reply_private(txt)
	except Transactions.NotEnoughMoney:
		req.reply_private(coinconfig["notenoughmoney"] % (amount, Transactions.balance(acct)))
	except Transactions.InsufficientFunds:
		req.reply_private("Something went wrong, report this to Magicking [%s]" % (token))
	except Transactions.NotEnoughGiftCard:
		req.reply_private("There is not enough gift card at that price")
		Logger.irclog("InsufficientFunds while executing '%s' from '%s'" % (req.text, req.nick))
commands["giftcard"] = giftcard

def withdraw(req, arg):
	"""%withdraw <address> [amount] - Sends 'amount' coins to the specified coin address. If no amount specified, sends the whole balance"""
	if len(arg) == 0:
		return req.reply(gethelp("withdraw"))
	acct = Irc.account_names([req.nick])[0]
	if not acct:
		return req.reply_private("You are not identified with freenode services (see /msg NickServ help)")
	if Transactions.lock(acct):
		return req.reply_private("Your account is currently locked")
	if len(arg) == 1:
		amount = max(Transactions.balance(acct) - 1, 1)
	else:
		try:
			amount = parse_amount(arg[1], acct, all_offset = -1)
		except ValueError as e:
			return req.reply_private(str(e))
	to = arg[0]
	if not Transactions.verify_address(to):
		return req.reply_private(to + " doesn't seem to be a valid %s address" % coinconfig['coinname'])
	token = Logger.token()
	try:
		tx = Transactions.withdraw(token, acct, to, amount)
		uri = coinconfig['explorertx'] % tx
		req.reply("Coins have been sent, see %s [%s]" % (uri, token))
	except Transactions.NotEnoughMoney:
		req.reply_private(coinconfig["notenoughmoney"] % (amount, Transactions.balance(acct)))
	except Transactions.InsufficientFunds:
		req.reply("Something went wrong, report this to mniip [%s]" % (token))
		Logger.irclog("InsufficientFunds while executing '%s' from '%s'" % (req.text, req.nick))
commands["withdraw"] = withdraw

def target_nick(target):
	return target.split("@", 1)[0]

def target_verify(target, accname):
	s = target.split("@", 1)
	if len(s) == 2:
		return Irc.equal_nicks(s[1], accname)
	else:
		return True

def tip(req, arg):
	"""%tip <target> <amount> - Sends 'amount' coins to the specified nickname. Nickname can be suffixed with @ and an account name, if you want to make sure you are tipping the correct person"""
	if len(arg) < 2:
		return req.reply(gethelp("tip"))
	to = arg[0]
	acct, toacct = Irc.account_names([req.nick, target_nick(to)])
	if not acct:
		return req.reply_private("You are not identified with freenode services (see /msg NickServ help)")
	if Transactions.lock(acct):
		return req.reply_private("Your account is currently locked")
	if not toacct:
		if toacct == None:
			return req.reply_private(target_nick(to) + " is not online")
		else:
			return req.reply_private(target_nick(to) + " is not identified with freenode services")
	if not target_verify(to, toacct):
		return req.reply_private("Account name mismatch")
	try:
		amount = parse_amount(arg[1], acct)
	except ValueError as e:
		return req.reply_private(str(e))
	token = Logger.token()
	try:
		Transactions.tip(token, acct, toacct, amount)
		if Irc.equal_nicks(req.nick, req.target):
			req.reply("Done [%s]" % (token))
		else:
			req.say(coinconfig["tipmsg"] % (req.nick, amount, target_nick(to), token))
		req.privmsg(target_nick(to), coinconfig["tipprivmsg"] % (req.nick, amount, token), priority = 10)
	except Transactions.NotEnoughMoney:
		req.reply_private(coinconfig["tiptry"] % (amount, Transactions.balance(acct)))
commands["tip"] = tip

def mtip(req, arg):
	"""%mtip <targ1> <amt1> [<targ2> <amt2> ...] - Send multiple tips at once"""
	if not len(arg) or len(arg) % 2:
		return req.reply(gethelp("mtip"))
	acct = Irc.account_names([req.nick])[0]
	if not acct:
		return req.reply_private("You are not identified with freenode services (see /msg NickServ help)")
	if Transactions.lock(acct):
		return req.reply_private("Your account is currently locked")
	for i in range(0, len(arg), 2):
		try:
			arg[i + 1] = parse_amount(arg[i + 1], acct)
		except ValueError as e:
			return req.reply_private(str(e))
	targets = []
	amounts = []
	total = 0
	for i in range(0, len(arg), 2):
		target = arg[i]
		amount = arg[i + 1]
		found = False
		for i in range(len(targets)):
			if Irc.equal_nicks(targets[i], target):
				amounts[i] += amount
				total += amount
				found = True
				break
		if not found:
			targets.append(target)
			amounts.append(amount)
			total += amount
	balance = Transactions.balance(acct)
	if total > balance:
		return req.reply_private(coinconfig["tiptry"] % (total, balance))
	accounts = Irc.account_names([target_nick(target) for target in targets])
	totip = {}
	failed = ""
	tipped = ""
	for i in range(len(targets)):
		if accounts[i] == None:
			failed += " %s (offline)" % (target_nick(targets[i]))
		elif accounts[i] == False:
			failed += " %s (unidentified)" % (target_nick(targets[i]))
		elif not target_verify(targets[i], accounts[i]):
			failed += " %s (mismatch)" % (targets[i])
		else:
			totip[accounts[i]] = totip.get(accounts[i], 0) + amounts[i]
			tipped += " %s %d" % (target_nick(targets[i]), amounts[i])
	token = Logger.token()
	try:
		Transactions.tip_multiple(token, acct, totip)
		tipped += " [%s]" % (token)
	except Transactions.NotEnoughMoney:
		return req.reply_private(coinconfig["tiptry"] % (total, Transactions.balance(acct)))
	output = "Tipped:" + tipped
	if len(failed):
		output += "  Failed:" + failed
	req.reply(output)
commands["mtip"] = mtip

def rain(req, arg):
	"""%rain <amount> [minutes=60] - Sends 'amount' coins to the specified channel. 'amount' will be equally distributed among users who has spoke in the last 'minutes', -1 for inactives users"""
	if len(arg) < 1:
		return req.reply(gethelp("rain"))
	t = -1
	try:
		active_delta = int(arg[1]) if len(arg) > 1 else Config.config.get("active_time", 60)
		if active_delta == -1: # pass -1 to include non active account on channel"
			pass
		elif active_delta <= 0:
			raise ValueError(repr(active_delta) + " - invalid minutes (should be 1 or more)")
		else:
			t = time.time() - active_delta * 60
	except ValueError as e:
		return req.reply_private(str(e))
	acct = Irc.account_names([req.nick])[0]
	if not acct:
		return req.reply_private("You are not identified with freenode services (see /msg NickServ help)")
	if Transactions.lock(acct):
		return req.reply_private("Your account is currently locked")
	try:
		amount = parse_amount(arg[0], acct)
	except ValueError as e:
		return req.reply_private(str(e))
	token = Logger.token()
	active_users = []
	with Global.account_lock:
		if Global.account_cache.get(req.target, None) != None:
			for user in Global.account_cache[req.target]:
				totip = Global.account_cache[req.target][user]
				if (totip != None and
					totip['account'] != acct and
					totip["account"] != False and
					totip["account"] != Config.config["account"] and
					totip["last_msg"] > t):
					active_users.append((totip["account"], user))
		else:
			return req.reply_private("rain command must be used in a channel")
	if len(active_users) == 0:
		return req.reply_private("Oh no ! You are all alone !")
	balance = Transactions.balance(acct)
	if amount > balance:
		return req.reply_private(coinconfig["tiptry"] % (amount, balance))
	amount_per_user = amount / len(active_users)
	amount -= amount_per_user * len(active_users)
	totip = {}
	for account, _ in active_users:
		totip[account] = totip.get(account, 0) + amount_per_user
	tipped = ""
	try:
		Transactions.tip_multiple(token, acct, totip)
		tipped += " [%s]" % (token)
	except Transactions.NotEnoughMoney:
		return req.reply_private(coinconfig["tiptry"] % (total, Transactions.balance(acct)))
	nicknames = [x for _, x in active_users]
	req.say(coinconfig["raining"] % (" ".join(nicknames), amount_per_user, token))
commands["rain"] = rain

def donate(req, arg):
	"""%donate <amount> - Donate 'amount' coins to help fund the server Doger is running on"""
	if len(arg) < 1:
		return req.reply(gethelp("donate"))
	acct = Irc.account_names([req.nick])[0]
	if not acct:
		return req.reply_private("You are not identified with freenode services (see /msg NickServ help)")
	if Transactions.lock(acct):
		return req.reply_private("Your account is currently locked")
	toacct = "@DONATIONS"
	try:
		amount = parse_amount(arg[0], acct)
	except ValueError as e:
		return req.reply_private(str(e))
	token = Logger.token()
	try:
		Transactions.tip(token, acct, toacct, amount)
		req.reply(coinconfig["donated"] % (amount, token))
	except Transactions.NotEnoughMoney:
		req.reply_private(coinconfig["tiptry"] % (amount, Transactions.balance(acct)))
commands["donate"] = donate

def gethelp(name):
	if name[0] == Config.config["prefix"]:
		name = name[1:]
	cmd = commands.get(name, None)
	if cmd and cmd.__doc__:
		return cmd.__doc__.split("\n")[0].replace("%", Config.config["prefix"])

def _help(req, arg):
	"""%help - list of commands; %help <command> - help for specific command"""
	if len(arg):
		h = gethelp(arg[0])
		if h:
			req.reply(h)
	else:
		if not Irc.equal_nicks(req.target, req.nick):
			return req.reply(coinconfig["help1"])
		acct = Irc.account_names([req.nick])[0]
		if acct:
			ident = "you're identified as \2" + acct + "\2"
		else:
			ident = "you're not identified"
		req.say(coinconfig["help2"].replace("%", Config.config["prefix"]))
		req.say((coinconfig["help3"] % (ident)).replace("%", Config.config["prefix"]))
commands["help"] = _help

def admin(req, arg):
	"""
	admin"""
	if len(arg):
		command = arg[0]
		arg = arg[1:]
		if command == "reload":
			for mod in arg:
				reload(sys.modules[mod])
			req.reply("Reloaded")
		elif command == "exec" and Config.config.get("enable_exec", None):
			exec(" ".join(arg).replace("$", "\n"))
		elif command == "ignore":
			Irc.ignore(arg[0], int(arg[1]))
			req.reply("Ignored")
		elif command == "die":
			for instance in Global.instances:
				Global.manager_queue.put(("Disconnect", instance))
			Global.manager_queue.join()
			Blocknotify.stop()
			Global.manager_queue.put(("Die",))
		elif command == "restart":
			for instance in Global.instances:
				Global.manager_queue.put(("Disconnect", instance))
			Global.manager_queue.join()
			Blocknotify.stop()
			os.execv(sys.executable, [sys.executable] + sys.argv)
		elif command == "manager":
			for cmd in arg:
				Global.manager_queue.put(cmd.split("$"))
			req.reply("Sent")
		elif command == "raw":
			Irc.instance_send(req.instance, eval(" ".join(arg)))
		elif command == "config":
			if arg[0] == "save":
				os.rename("Config.py", "Config.py.bak")
				with open("Config.py", "w") as f:
					f.write("config = " + pprint.pformat(Config.config) + "\n")
				req.reply("Done")
			elif arg[0] == "del":
				exec("del Config.config " + " ".join(arg[1:]))
				req.reply("Done")
			else:
				try:
					req.reply(repr(eval("Config.config " + " ".join(arg))))
				except SyntaxError:
					exec("Config.config " + " ".join(arg))
					req.reply("Done")
		elif command == "join":
			Irc.instance_send(req.instance, ("JOIN", arg[0]), priority = 0.1)
		elif command == "part":
			Irc.instance_send(req.instance, ("PART", arg[0]), priority = 0.1)
		elif command == "caches":
			if len(arg) > 0 and arg[0] == "generate":
				arg = arg[1:]
				whois_list = []
				with Global.account_lock:
					if len(arg) == 0 or arg[0] == "*":
						for channel in Global.account_cache:
							for nick in Global.account_cache[channel]:
								whois_list.append(nick)
					else:
						for item in arg:
							if item[0] == "#" and item in Global.account_cache:
								for nick in Global.account_cache[item]:
									whois_list.append(nick)
							else:
								whois_list.append(item)
				Irc.account_names(whois_list)
			acsize = 0
			accached = 0
			with Global.account_lock:
				for channel in Global.account_cache:
					for user in Global.account_cache[channel]:
						acsize += 1
						if Global.account_cache[channel][user] != None:
							accached += 1
			acchannels = len(Global.account_cache)
			whois = " OK"
			whoisok = True
			for instance in Global.instances:
				tasks = Global.instances[instance].whois_queue.unfinished_tasks
				if tasks:
					if whoisok:
						whois = ""
						whoisok = False
					whois += " %s:%d!" % (instance, tasks)
			req.reply("Account caches: %d user-channels (%d cached) in %d channels; Whois queues:%s" % (acsize, accached, acchannels, whois))
		elif command == "channels":
			inss = ""
			for instance in Global.instances:
				chans = []
				with Global.account_lock:
					for channel in Global.account_cache:
						if instance in Global.account_cache[channel]:
							chans.append(channel)
				inss += " %s:%s" % (instance, ",".join(chans))
			req.reply("Instances:" + inss)
		elif command == "balances":
			database, dogecoind = Transactions.balances()
			req.reply("Coin daemon: %.8f; Database: %.8f" % (dogecoind, database))
		elif command == "blocks":
			info, hashd = Transactions.get_info()
			hashb = Transactions.lastblock.encode("ascii")
			req.reply("Best block: " + hashd + ", Last tx block: " + hashb + ", Blocks: " + str(info['blocks']) + ", Testnet: " + str(info['testnet']))
		elif command == "lock":
			if len(arg) > 1:
				if arg[1] == "on":
					Transactions.lock(arg[0], True)
				elif arg[1] == "off":
					Transactions.lock(arg[0], False)
				req.reply("Done")
			elif len(arg):
				req.reply("locked" if Transactions.lock(arg[0]) else "not locked")
		elif command == "ping":
			t = time.time()
			Irc.account_names(["."])
			pingtime = time.time() - t
			acc = Irc.account_names([req.nick])[0]
			t = time.time()
			Transactions.balance(acc)
			dbreadtime = time.time() - t
			t = time.time()
			Transactions.lock(acc, False)
			dbwritetime = time.time() - t
			t = time.time()
			Transactions.ping()
			rpctime = time.time() - t
			req.reply("Ping: %f, DB read: %f, DB write: %f, RPC: %f" % (pingtime, dbreadtime, dbwritetime, rpctime))
		elif command == "gift":
			if len(arg) == 4:
				req.reply(str(Transactions.add_gift(arg[0], arg[1], arg[2], arg[3])))



commands["admin"] = admin

def _as(req, arg):
	"""
	admin"""
	_, target, text = req.text.split(" ", 2)
	if target[0] == '@':
		Global.account_cache[""] = {"@": target[1:]}
		target = "@"
	if text.find(" ") == -1:
		command = text
		args = []
	else:
		command, args = text.split(" ", 1)
		args = [a for a in args.split(" ") if len(a) > 0]
	if command[0] != '_':
		cmd = commands.get(command.lower(), None)
		if not cmd.__doc__ or cmd.__doc__.find("admin") == -1 or Irc.is_admin(source):
			if cmd:
				req = Hooks.FakeRequest(req, target, text)
				Hooks.run_command(cmd, req, args)
	if Global.account_cache.get("", None):
		del Global.account_cache[""]
commands["as"] = _as
