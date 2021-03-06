from Houdini.Handlers import Handlers, XML
from Houdini.Data.Penguin import Penguin, NameApproval
from Houdini.Data.Ban import Ban
from Houdini.Data.Login import Login
from Houdini.Data.Membership import Membership
from Houdini.Data.Postcard import Postcard
from Houdini.Data.Timer import Timer
from Houdini.Crypto import Crypto
from Houdini.Data import retryableTransaction

import bcrypt, time, os

from datetime import datetime, timedelta
from sqlalchemy import or_

@Handlers.Handle(XML.Login)
@retryableTransaction()
def handleLogin(self, data):
    if self.randomKey is None:
        return self.transport.loseConnection()

    if not hasattr(self.server, "loginAttempts"):
        self.server.loginAttempts = {}

    loginTimestamp = time.time()
    username = data.Username
    password = data.Password

    self.logger.info("{0} is attempting to login..".format(username))

    self.session.commit()
    user = self.session.query(Penguin).filter_by(Username=username).first()

    if user is None:
        return self.sendErrorAndDisconnect(100)

    ipAddr = self.transport.getPeer().host

    if not bcrypt.checkpw(password, user.Password):
        self.logger.info("{} failed to login.".format(username))

        if ipAddr in self.server.loginAttempts:
            lastFailedAttempt, failureCount = self.server.loginAttempts[ipAddr]

            failureCount = 1 if loginTimestamp - lastFailedAttempt >= self.server.server["LoginFailureTimer"] \
                else failureCount + 1

            self.server.loginAttempts[ipAddr] = [loginTimestamp, failureCount]

            if failureCount >= self.server.server["LoginFailureLimit"]:
                return self.sendErrorAndDisconnect(150)

        else:
            self.server.loginAttempts[ipAddr] = [loginTimestamp, 1]

        return self.sendErrorAndDisconnect(101)

    if ipAddr in self.server.loginAttempts:
        previousAttempt, failureCount = self.server.loginAttempts[ipAddr]

        maxAttemptsExceeded = failureCount >= self.server.server["LoginFailureLimit"]
        timerSurpassed = (loginTimestamp - previousAttempt) > self.server.server["LoginFailureTimer"]

        if maxAttemptsExceeded and not timerSurpassed:
            return self.sendErrorAndDisconnect(150)
        else:
            del self.server.loginAttempts[ipAddr]

    if user.Permaban:
        return self.sendErrorAndDisconnect(603)

    activeBan = self.session.query(Ban).filter(Ban.PenguinID == user.ID)\
        .filter(Ban.Expires >= datetime.now()).first()

    if activeBan is not None:
        hoursLeft = round((activeBan.Expires - datetime.now()).total_seconds() / 60 / 60)

        if hoursLeft == 0:
            return self.sendErrorAndDisconnect(602)

        else:
            self.sendXt("e", 601, hoursLeft)
            return self.transport.loseConnection()

    loginsToday = self.session.query(Login).filter(Login.PenguinID == user.ID).order_by(Login.Date.desc()).first()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    timer = self.session.query(Timer).filter(Timer.PenguinID == user.ID).first()

    if timer is not None and timer.TimerActive == 1:
        # Reset minutes played today to zero if it's their first login of the day
        if str(today) not in str(loginsToday.Date):
            minutesToday = 0
            self.session.query(Timer.MinutesToday).filter(Timer.PenguinID == user.ID).update({"MinutesToday": minutesToday})
            self.session.commit()
        else:
            minutesToday = timer.MinutesToday
        if timer.TotalDailyTime != 0:
            if timer.MinutesToday >= timer.TotalDailyTime:
                dailyHours = int(timer.TotalDailyTime / 60)
                dailyMins = timer.TotalDailyTime - (dailyHours * 60)
                total = str(dailyHours) + ":" + str(dailyMins)
                self.sendXt("e", 910, total)
                return self.transport.loseConnection()

        if timer.PlayHourStart >= datetime.utcnow().time() or timer.PlayHourEnd <= datetime.utcnow().time():
            startHours = datetime.strptime(str(timer.PlayHourStart), "%H:%M:%S")
            endHours = datetime.strptime(str(timer.PlayHourEnd), "%H:%M:%S")
            offsetStartHours = format(startHours + timedelta(hours=timer.UTCOffset), "%H:%M:%S")
            offsetEndHours = format(endHours + timedelta(hours=timer.UTCOffset), "%H:%M:%S")
            self.sendXt("e", 911, offsetStartHours, offsetEndHours)
            return self.transport.loseConnection()

    self.logger.info("{} logged in successfully".format(username))

    randomKey = Crypto.generateRandomKey()
    loginKey = Crypto.hash(randomKey[::-1])

    self.session.add(user)

    self.user = user
    self.user.LoginKey = loginKey
    self.user.ConfirmationHash = Crypto.hash(os.urandom(24))
    friendsKey = Crypto.hash(os.urandom(24))

    self.session.commit()

    nameApproval = self.session.query(NameApproval).filter(NameApproval.PenguinID == user.ID).first()

    langs = [nameApproval.ru, nameApproval.de, nameApproval.es, nameApproval.fr, nameApproval.pt, nameApproval.en]
    approval = [0 if i < 0 else i for i in langs]
    rejection = [1 if i < 0 else 0 for i in langs]

    languageApprovedBits = "{}{}0{}{}{}{}".format(*approval)
    languageRejectedBits = "{}{}0{}{}{}{}".format(*rejection)
    languageApprovedBitmask = int(languageApprovedBits, 2)
    languageRejectedBitmask = int(languageRejectedBits, 2)

    self.user.Approval = languageApprovedBitmask

    membership = self.session.query(Membership).filter_by(PenguinID=user.ID).first()
    if membership.Status == 1:
        membershipEnd = datetime.strptime(str(membership.End), "%Y-%m-%d %H:%M:%S")
        membershipLeft = membershipEnd - datetime.utcnow()
        if membershipLeft.days < 0:
            membership.Status = 0
            membership.CurrentPlan = 0
            membership.Start = None
            membership.End = None
            membership.Postcards = 0
            postcard = Postcard(RecipientID=self.user.ID, SenderID=None, Type=124)
            self.session.add(postcard)
            self.session.commit()
        elif membershipLeft.days <= 7:
            if membership.Postcards == 1:
                membership.Postcards = 2
                postcard = Postcard(RecipientID=self.user.ID, SenderID=None, Type=123)
                self.session.add(postcard)
                self.session.commit()
        elif membershipLeft.days <= 14:
            if membership.Postcards == 0:
                membership.Postcards = 1
                postcard = Postcard(RecipientID=self.user.ID, SenderID=None, Type=122)
                self.session.add(postcard)
                self.session.commit()

    worldPopulations = []

    serversConfig = self.server.config["Servers"]

    for serverName in serversConfig.keys():
        if serversConfig[serverName]["World"]:
            serverPopulation = self.server.redis.get("%s.population" % serverName)

            if not serverPopulation is None:
                serverPopulation = int(serverPopulation) / (serversConfig[serverName]["Capacity"] / 6)
            else:
                serverPopulation = 0

            serverPlayers = self.server.redis.smembers("%s.players" % serverName)

            worldPopulations.append("%s,%s" % (serversConfig[serverName]["Id"], serverPopulation))

    rawLoginData = "|".join([str(user.ID), str(user.ID), user.Username,
                             user.LoginKey, str(), str(languageApprovedBitmask), str(languageRejectedBitmask)])

    if user.Active == 0:
        regDate = datetime.strptime(str(user.RegistrationDate), "%Y-%m-%d %H:%M:%S")
        trialExpiry = regDate + timedelta(days=7)
        trialLeft = trialExpiry - datetime.utcnow()
        hoursLeft = ((trialLeft.days * 24) + (trialLeft.seconds / 3600))
        if hoursLeft <= 0:
            return self.sendXt("loginMustActivate", 0, user.ConfirmationHash, rawLoginData, "email@address.org")
        else:
            remaining = "{}|0|0".format(hoursLeft)
            return self.sendXt("l", rawLoginData, user.ConfirmationHash, friendsKey, "|".join(worldPopulations), "email@address.org", remaining)
    else:
        self.sendXt("l", rawLoginData, user.ConfirmationHash, friendsKey, "|".join(worldPopulations), "email@address.org")
