from Houdini.Handlers import Handlers, XT
from Houdini.Data.Penguin import Penguin, IgnoreList

@Handlers.Handle(XT.GetIgnoreList)
@Handlers.Throttle(-1)
def handleGetIgnoreList(self, data):
    ignoreString = "%".join(["{}|{}".format(ignoreId, ignoreNickname) for ignoreId, ignoreNickname in self.ignore.items()])
    self.sendXt("gn", ignoreString)

@Handlers.Handle(XT.AddIgnore)
def handleAddIgnore(self, data):
    if data.PlayerId in self.buddies:
        return

    if data.PlayerId in self.ignore:
        return

    ignoreUser = self.session.query(Penguin.Nickname, Penguin.ID).\
        filter(Penguin.ID == data.PlayerId).first()

    self.ignore[data.PlayerId] = ignoreUser.Nickname

    ignore = IgnoreList(PenguinID=self.user.ID, IgnoreID=ignoreUser.ID)
    self.session.add(ignore)

@Handlers.Handle(XT.RemoveIgnore)
def handleRemoveIgnore(self, data):
    if data.PlayerId not in self.ignore:
        return

    del self.ignore[data.PlayerId]

    self.session.query(IgnoreList).filter_by(PenguinID=self.user.ID, IgnoreID=data.PlayerId).delete()
