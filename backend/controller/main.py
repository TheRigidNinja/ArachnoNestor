from controller import ArachnoNestor
from time import sleep
if __name__=='__main__':
    bot = ArachnoNestor([1])
    # example: move all motors at 1500 RPM forward:
    bot.engage_motors(100, forward=True)
    # ... your logic here ...
    # then stop:


    sleep(5)
    bot.stop_all(brake=False)
    # bot.stop_all(brake=True)
