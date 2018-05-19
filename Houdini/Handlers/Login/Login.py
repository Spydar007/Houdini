from Houdini.Handlers import Handlers, XML
from Houdini.Data.Penguin import Penguin, BuddyList
from Houdini.Data.Ban import Ban
from Houdini.Crypto import Crypto
from Houdini.Data import retryableTransaction

import bcrypt, time, os

from datetime import datetime

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

    if not user.Active:
        return self.sendErrorAndDisconnect(900)

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

    self.logger.info("{} logged in successfully".format(username))

    randomKey = Crypto.generateRandomKey()
    loginKey = Crypto.hash(randomKey[::-1])

    self.session.add(user)

    self.user = user
    self.user.LoginKey = loginKey
    self.user.ConfirmationHash = Crypto.hash(os.urandom(24))

    self.session.commit()

    buddyWorlds = []
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

            if not len(serverPlayers) > 0:
                self.logger.debug("Skipping buddy iteration for %s " % serverName)
                continue

            buddies = self.session.query(BuddyList.BuddyID).filter(BuddyList.PenguinID == self.user.ID)
            for buddyId, in buddies:
                if str(buddyId) in serverPlayers:
                    buddyWorlds.append(serversConfig[serverName]["Id"])
                    break

    rawLoginData = "|".join([str(user.ID), str(user.ID), user.Username,
                             user.LoginKey, str(user.Approval), str(0 if bool(user.Approval) else 1)])
    self.sendXt("l", rawLoginData, user.ConfirmationHash, "friendsKey", "|".join(worldPopulations), "email@address.org")
