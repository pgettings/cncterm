# HAL interface for cncterm 


import linuxcnc # for constants in poll()
import hal

class HALInterface:
    def __init__(self, cmd, stat):
	self.cmd = cmd; # command channel for sending motion commands
	self.stat = stat; # status channel from motion controller
        self.c = hal.component("cncterm")
        self.c.newpin("status-light", hal.HAL_BIT, hal.HAL_OUT)
        self.c.newpin("jog.active", hal.HAL_BIT, hal.HAL_OUT)
        self.c.newpin("jog.wheel.x", hal.HAL_BIT, hal.HAL_OUT)
        self.c.newpin("jog.wheel.y", hal.HAL_BIT, hal.HAL_OUT)
        self.c.newpin("jog.wheel.z", hal.HAL_BIT, hal.HAL_OUT)
        self.c.newpin("jog.wheel.a", hal.HAL_BIT, hal.HAL_OUT)
        self.c.newpin("jog.wheel.increment", hal.HAL_FLOAT, hal.HAL_OUT)

        self.c.newpin("cycle-start", hal.HAL_BIT, hal.HAL_IN)
        self.cyclestart = 0
        self.c.newpin("abort", hal.HAL_BIT, hal.HAL_IN)
        self.abort = 0
        self.c.newpin("single-block", hal.HAL_BIT, hal.HAL_IN)
        self.singleblock = 0
        self.c.newpin("wheel-counts", hal.HAL_S32, hal.HAL_IN)
        self.counts = 0
        self.jog_velocity = 1
        self.c.ready()
        self.active = 0 # jogging active?

    def wheel(self):
        counts = self.c["wheel-counts"]/4
        ret = counts - self.counts
        self.counts = counts
        return ret

    def set_axis(self, n):
        self.c["jog.wheel.x"] = n == 0 and self.active
        self.c["jog.wheel.y"] = n == 1 and self.active
        self.c["jog.wheel.z"] = n == 2 and self.active
        self.c["jog.wheel.a"] = n == 3 and self.active

    def jog_step(self, inc):
        self.c["jog.wheel.increment"] = inc

    def jog_active(self, active):
        self.active = active

    def poll(self):
        abort = self.c["abort"]
        if abort and not self.abort:
  	  self.cmd.abort()
	  return
        self.abort = abort

        singleblock = self.c["single-block"]
	# if single block status changed, update the control
        if singleblock ^ self.singleblock:
	  if self.stat.queue > 0 or self.stat.paused:
	    # program or mdi is running
	    if singleblock:
	      self.cmd.auto(self.emc.AUTO_PAUSE)
	    else:
	      self.auto(self.emc.AUTO_RESUME)
        self.singleblock = singleblock

	# if cyclestart status changed, update the control
        cyclestart = self.c["cycle-start"]
        if cyclestart and not self.cyclestart:
	  if self.stat.paused: # if paused, resume or step
	    if self.singleblock:
	      self.cmd.auto(self.emc.AUTO_STEP)
	    else:
	      self.cmd.auto(self.emc.AUTO_RESUME)
	    self.cyclestart = cyclestart
	    return
	  if self.stat.interp_state == linuxcnc.INTERP_IDLE: # if idle, run program
	    self.cmd.mode(linuxcnc.MODE_AUTO)
	    self.cmd.wait_complete()
	    if self.singleblock: # run in single block
	      self.cmd.auto(linuxcnc.AUTO_STEP)
	    else:
	      self.cmd.auto(linuxcnc.AUTO_RUN)
        self.cyclestart = cyclestart

        self.c["jog.active"] = self.stat.task_mode == linuxcnc.MODE_MANUAL

        if self.stat.paused:
            # blink
            self.c["status-light"] = not self.c["status-light"]
        else:
            if self.stat.queue > 0 or self.stat.interp_state != linuxcnc.INTERP_IDLE:
                # something is running
                self.c["status-light"] = 1
            else:
                # nothing is happening
                self.c["status-light"] = 0
