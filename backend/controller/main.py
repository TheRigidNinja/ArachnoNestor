from controller import ArachnoNestor

if __name__=='__main__':
    bot = ArachnoNestor([1,2,3,4])
    # example: move all motors at 1500 RPM forward:
    bot.engage_motors(1500, forward=True)
    # ... your logic here ...
    # then stop:
    bot.stop_all(brake=False)
