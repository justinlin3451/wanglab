'''
<device>
<Class>rail</Class><Group>rail</Group><max>30000</max><min>0</min><speed type="control"><Method>W</Method><Expression>0</Expression></speed><Addr>1</Addr><P1 type="control"><Method>W/R</Method><Expression>0</Expression></P1>
</device>
'''
# -*- coding: utf-8 -*-
import PyDriverCom
import random

# for PyDriver37 only
# use following code to run the single thread as you need
# PyDriverCom.sth.method = yourmethod
# PyDriverCom.sth.RunFlag = True

class pyDriver:
    def __init__(self):
        self.Addr = "1"
        self.speed = 50

    def initialize(self):
        try:
        ########################
        # Write your code here #
            self.Addr = self.ParamList["addr"]
            self.speed = self.ParamList["speed_expression"]
            self.SendData(self.Addr + " " + str(random.randint(1, 32767)) + " reset_all\n")
            self.SendData(self.Addr + " " + str(random.randint(1, 32767)) + " speed " + self.speed + "\n")
            self.min = int(self.ParamList["min"])
            self.max = int(self.ParamList["max"])
        ########################
        except Exception as e:
            print(e)
        return

    def end(self):
        # Handle end event after close ports
        try:
        ########################
        # Write your code here #
            pass
        ########################
        except Exception as e:
            print(e)
        return

    def getValue(self,control):
        # 'control' is a string indicate which control of the device will be gotten
        # self.SendData(string or byte[] "your get value cmd")
        try:
        ########################
        # Write your code here #
            self.SendData(self.Addr + " 1 get\n")
        ########################
        except Exception as e:
            print(e)
        return

    def setValue(self,control, sv):
        # 'control' is a string indicate which control of the device will be set
        # 'sv' is an object indicate a value will be set to the device
        # If you have chosen "IOGeneral" as the device type, 'sv' actually is a string
        # self.SendData(string or byte[] "your set value cmd")
        try:
        ########################
        # Write your code here #
            if control == "speed":
                self.speed = str(sv)
                self.SendData(self.Addr + " " + str(random.randint(1, 32767)) + " speed " + self.speed + "\n")
            elif control == "p1":
                if int(sv)>=self.min and int(sv)<=self.max:
                    self.SendData(self.Addr + " " + str(random.randint(1, 32767)) + " M1 " + str(sv) + "\n")
    ##            elif control == "p2":
    ##                self.SendData(self.Addr + " " + str(random.randint(1, 32767)) + " P2 " + str(sv) + "\n")
    ##            elif control == "p3":
    ##                self.SendData(self.Addr + " " + str(random.randint(1, 32767)) + " P3 " + str(sv) + "\n")
    ##            elif control == "p4":
    ##                self.SendData(self.Addr + " " + str(random.randint(1, 32767)) + " P4 " + str(sv) + "\n")
    ##            elif control == "p5":
    ##                self.SendData(self.Addr + " " + str(random.randint(1, 32767)) + " P5 " + str(sv) + "\n")
    ##            elif control == "p6":
    ##                self.SendData(self.Addr + " " + str(random.randint(1, 32767)) + " P6 " + str(sv) + "\n")
        ########################
        except Exception as e:
            print(e)
        return

    def receiver(self,rawdata):
        # 'rawdata' is an object returned by your device
        # Depands on your device, 'rawdata' may be a string or byte[]
        # Handle 'rawdata' to assign 'control' and 'result'
        # Typically, 'result' can be string, bool, int, and double
        # self.DataReturn(string control, object result)
        # both self.ShowMsg(object) and print(object) are for your debug. Click red button in the main window to show the message.
        try:
        ########################
        # Write your code here #
            data = str(bytes(rawdata))[2:-3]
            #print(data)
            data = data.split()
            if data[0]==self.Addr and data[2]=="get":
                self.DataReturn('p1',int(data[3]))
##                self.DataReturn('p2',int(data[4]))
##                self.DataReturn('p3',int(data[5]))
##                self.DataReturn('p4',int(data[6]))
##                self.DataReturn('p5',int(data[7]))
##                self.DataReturn('p6',int(data[8]))
        ########################
        except Exception as e:
            print(e)
        return
