# xDomeAlert_WebApp
A Web Application displaying alerts and events from Claroty xDome

Installation:
```
cd ~/Documents
mkdir xDomeAlert_WebApp
git clone https://github.com/icsdef3nder/xDomeAlert_WebApp.git
```

Prepare user and groups:
The solution is using the Linux PAM for authentication and authorization. Create the following groups:
- xDome-Viewer
- xDome-Admin

The xDome-Admin will have acccess to the settings page for setting up the appliction. The xDome-Viewer will have access to the application without the settings menu.

Run the application:
```
cd ~/Documents/xDomeAlert_WebApp
source venv/bin/activate
python3 ./run.py
```

Open Web App in browser:

http://127.0.0.1:5000

Read USER_MANUAL.md

