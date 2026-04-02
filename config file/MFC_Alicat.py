'''
<device>
<Class>gas</Class><Group>H2</Group><flow type="control"><Method>W/R</Method><Expression>0</Expression><max>100</max><addr>2</addr></flow>
</device>
'''
# -*- coding: utf-8 -*-
import PyDriverCom

# for PyDriver37 only
# use following code to run the single thread as you need
# PyDriverCom.sth.method = yourmethod
# PyDriverCom.sth.RunFlag = True

class pyDriver:
    def __init__(self, *args):
        pass
        
    def initialize(self):
        # Handle initialize event after open ports

        # self.ParamList is a Dictionary<string key, string value> object
        # It provides the parameters of this device shown in the ConfigFile, defined as following
        # {
        #  "key1":"value",
        #  "key2":"value",
        #  ...
        #  "control1_subkey1":"value",
        #  "control1_subkey2":"value",
        #  ...
        #  "control2_subkey1":"value",
        #  "control2_subkey2":"value",
        #  ...
        # }
        # All keys have been converted to lower case for safety reason
        # Using self.ParamList["key"] to get value
        try:
        ########################
        # Write your code here #
            self.period = 64000
            self.addr = str(chr(int(self.ParamList["flow_addr"]) + 64))
            self.max = float(self.ParamList["flow_max"])
            self.SendData(self.addr + "W16=18119\r")
        ########################
        except Exception as e:
            print(e)
        return

    def end(self):
        # Handle end event after close ports
        try:
        ########################
        # Write your code here #
            self.SendData(self.addr + "W16=199\r")
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
            if control=="flow":
                self.SendData(self.addr+"\r")
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
            sv = float(sv)
            if control=="flow":
                if sv >= self.max:
                    sv = self.max
                if sv <= 0:
                    sv = 0
                width = int(sv * self.period / self.max )
                self.SendData(self.addr+str(width)+"\r")
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
        # self.CrossRef(string device, string control) will return the last value of the specific control of the device
        # self.CrossCtrl(string device, string control, string sv) will set a value for the specific control of the device
        # both self.ShowMsg(object) and print(object) are for your debug. Click red button in the main window to show the message. 
        try:
        ########################
        # Write your code here #
            data=str(bytes(rawdata))
            args = data[:-1].split()
            if args[0]==self.addr and len(args)>=3 and args[2]!="=":
                # self.DataReturn("pressure", float(args[1]))
                self.DataReturn("flow", float(args[2]))
        ########################
        except Exception as e:
            print(e)
        return
