-- Trading Desk for the Dock. Opening it starts the desk and opens the dashboard.
-- Clicking it again reopens the dashboard. Quitting it (Dock, right-click, Quit) stops the desk.
-- Built by tools/make_app.sh, which fills in the path below.

global deskPort

on run
	set deskPort to ""
	startDesk()
end run

on reopen
	if deskPort is "" then
		startDesk()
	else
		open location "http://127.0.0.1:" & deskPort & "/"
	end if
end reopen

on startDesk()
	try
		set deskPort to do shell script quoted form of "__CONTROL__" & " start"
		open location "http://127.0.0.1:" & deskPort & "/"
	on error errMsg
		display alert "Trading Desk could not start" message errMsg as warning
		set deskPort to ""
		quit
	end try
end startDesk

on idle
	return 60
end idle

on quit
	try
		do shell script quoted form of "__CONTROL__" & " stop"
	end try
	set deskPort to ""
	continue quit
end quit
