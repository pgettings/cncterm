#!/usr/bin/python2
#
# LinuxCNC interface for serial terminals
#
# (C) 2022-2023 P Gettings
# 
# See LinuxCNC for licensing terms.
#

#
# Curses-based client for linuxcnc
#
# Designed to be run with a keyboard and HAL buttons.  Like touchy, but
# with a keyboard instead of a mouse or touchscreen. Thus, can be used
# on serial terminals, such as a TTL to serial converter on the debug
# terminal pins of a BeagleBone.
#

# FIXME - add backplot screen; convert listing to motions that are fed
# to a gnuplot process to draw as "sixels" which are then blitted to the
# screen. See "sixel" on wikipedia, and update ASCII terminal firmware
# to add sixel drawing....
# FIXME - add macro facility, which is most excellent for probing
# quickly. Grab macro filenames from ini file, and then make dialog box
# to choose which macro? Load and run the macro file as a normal
# program....
# FIXME - add ability to page through tool table, program listing with
# pgup and pgdn keys, a screen at a time.


# built-in modules
import sys, string, time
import traceback
import curses
import curses.ascii
import os
import math

# program specific modules
import linuxcnc
import hal
import gcode
import cmds
import curses_dialog # dialog boxes for input
from hal_interface import HALInterface # HAL pins, etc.

class Interface:
  def __init__(self):
    self.tabs = ["Default", "Program", "Status", "Tools"]
    self.tab = 0
    self.s = None # curses window object for the whole screen
    self.sleep = 0.3 # sleep period, in seconds
    self.maxx = 0
    self.maxy = 0
    self.ini = None # linuxcnc ini file object
    self.spindle = 0 # active spindle number, starts at 0
    self.stat = linuxcnc.stat()
    self.cmd = linuxcnc.command()
    self.err = linuxcnc.error_channel()
    self.hal = None # HAL interface object
    self.jogging = False
    self.jog_increments = [0.0001, 0.001, 0.010]; # jogging increments, inches
    self.jog_index = len(self.jog_increments)-1 # start at last increment
    self.jog_step = 0.0
    self.axis = None # selected axis
    self.joint = None # selected joint
    self.limits_override = False # True if limits being overridden
    self.listing = [] # program listing lines
    self.inch = True; # machine is inch or mm
    self.g20 = True; # g code in inch or mm
    self.axis_ltrs = [] # fill from ini file TRAJ/COORDINATES
    self.n_axes = 1 # number of axes to display
    self.time = 0.0; # time of last update
    self.v = [0.0, 0.0, 0.0, 0.0, 0.0] # computed velocity
    self.old = [0.0, 0.0, 0.0, 0.0, 0.0] # old position for v calcs
    self.olderr = (None, "") # linuxcnc message type, string from error channel
    self.dispcount = 0
    self.tools = [] # tool table, missing empty entries from stat.tool_table
    self.program_start = 0 # program listing start index
    self.tool_start = 0 # tool listing start index

  def error_msg(self, msg):
    self.olderr = (linuxcnc.OPERATOR_ERROR, msg)
  
  def text_msg(self, msg):
    self.olderr = (linuxcnc.OPERATOR_TEXT, msg)

  def display_msg(self, msg):
    self.olderr = (linuxcnc.OPERATOR_DISPLAY, msg)

#################################
##### MAIN CODE BEGINS HERE #####
#################################
def main():
  global ui

  #
  # Setup linuxcnc streams
  #
  ui = Interface()
  # ui.stat is linuxcnc.stat()
  # ui.cmd is linuxcnc.command()
  # load ini file so can grab variables, later
  if len(sys.argv) < 3:
    sys.stderr.write("CNCTERM: Must specify ini file on command line!")
    sys.exit(0)
  inifile = sys.argv[2];
  sys.stderr.write("CNCTERM: starting up, inifile %s\n"%inifile)
  ui.ini = linuxcnc.ini(inifile)
  ui.sleep = float(ui.ini.find("EMCIO", "CYCLE_TIME")) or 0.1

  unit = ui.ini.find("TRAJ", "LINEAR_UNITS")
  if unit:
    if unit.lower() == "inch":
      ui.inch = True;
      sys.stderr.write("CNCTERM: machine units are inches\n");
    elif unit.lower() == "mm":
      ui.inch = False;
      sys.stderr.write("CNCTERM: machine units are mm\n");
    else:
      sys.stderr.write("CNCTERM: ini file unit setting '%s' unknown; assuming inch\n"%unit)
      ui.inch = True;
  else:
    sys.stderr.write("CNCTERM: ini file unit setting not found; assuming inch\n")
    ui.inch = True;

  # Get coordinate letters for axes
  coords = ui.ini.find("TRAJ", "COORDINATES")
  if coords:
    # letters is a string, space separated, so convert to list
    letters = coords.split()
    for i in range(len(letters)):
      ui.axis_ltrs.append(letters[i])
    ui.n_axes = len(letters)
  else:
    sys.stderr.write("CNCTERM: ini file missing TRAJ / COORDINATES. Die!\n")
    sys.exit(0)
  if ui.n_axes > 5:
    sys.stderr.write("CNCTERM: interface only usable up to 5 axes. Die!\n")
    sys.exit(0)

  # add hal pins, for jogging by wheel, and for buttons and lights
  ui.hal = HALInterface(ui.cmd, ui.stat)

  ui.hal.poll()
  ui.stat.poll()

  if ui.n_axes > ui.stat.axes:
    sys.stderr.write("CNCTERM: truncating axes from %d to %d. Die!\n"%(ui.n_axes, ui.stat.axes))
    ui.n_axes = ui.stat.axes

  # set maximum velocity from ini file, since otherwise set to +Inf
  max_v = float(ui.ini.find("TRAJ", "MAX_VELOCITY")) or 1.5
  sys.stderr.write("CNCTERM: setting max velocity to %.4f units/sec\n"%max_v)
  ui.cmd.maxvel(max_v)
  cycle_jog_steps() # start at last jog increment, update for mm

  #
  # run post-gui hal file, to setup MPG, etc.
  sys.stderr.write("CNCTERM: HAL pins created, running cncterm.hal...\n")
  os.spawnvp(os.P_WAIT, "halcmd", ["halcmd", "-f", "cncterm.hal"])

  pg_halfile = ui.ini.find("HAL", "POSTGUI_HALFILE")
  if pg_halfile:
    sys.stderr.write("CNCTERM: Running post-gui hal file '%s'...\n"%pg_halfile)
    os.spawnvp(os.P_WAIT, "halcmd", ["halcmd", "-i",inifile,"-f", pg_halfile])

  #
  # Init curses, set up basic format
  #
  sys.stderr.write("CNCTERM: Initialize curses interface...\n")
  ui.s = curses.initscr()
  curses.noecho()
  curses.cbreak()
  ui.s.nodelay(1)
  # interpret special keys in curses to constants
  ui.s.keypad(True)


  # get size of screen
  (ui.maxy, ui.maxx) = ui.s.getmaxyx()

  # Map it
  ui.s.refresh()

  # display it
  ui.display_msg("Reading tool table for entries....")
  cls()

  parse_tools()
  ui.display_msg("Done.")
  cls()

  # main loop
  while(1):
    # update hal
    ui.hal.poll()
    # poll current status
    ui.stat.poll()

    # current linuxcnc module does not have the G_xx constants like it should
    # determine if currently processing blocks as metric or inch
    if 200 in ui.stat.gcodes: # inch mode
      ui.g20 = True;
    elif 210 in ui.stat.gcodes: # mm mode
      ui.g20 = False;
    else: # problem if not in either mode
      sys.stderr.write("CNCTERM: FATAL ERROR: Current G codes has neither G20 or G21! DIE!\n");
      sys.exit(1)

    # handle commands, if any
    dispatcher()

    # update jogging status, etc.
    if ui.axis != None:
      # turn on jogging if nothing running
      if ui.stat.queue==0 and ui.stat.interp_state == linuxcnc.INTERP_IDLE:
        ui.jogging = True
	ui.hal.jog_active(True)
	ui.hal.set_axis(ui.axis)
      else:
        ui.hal.jog_active(False)

    # refresh the screen
    cls()
    # delay for a bit, to suck less CPU
    time.sleep(ui.sleep)

  # End of main
  ################################

#################################
##### FUNCTION CODE IS HERE #####
#################################
#

def dispatcher():
  global ui

  cmd = ui.s.getch() # grab character(s), check for control characters
  if cmd == curses.KEY_RESIZE:	# window resized; reset subwins & redraw
    (ui.maxy, ui.maxx) = ui.s.getmaxyx()
  elif cmd == curses.ascii.ESC or cmd == curses.ascii.BS: # abort
    cmds.abort(ui.cmd)
  elif cmd == curses.ascii.TAB: # tab to next display tab
    next_screen()
  elif cmd == curses.ascii.LF: # clear messages, redraw
    ui.dispcount = 0
    ui.olderr = (None, "")
    ui.s.clear()
    ui.s.redrawwin()
    cls()
  elif cmd == curses.KEY_UP: # jog Y+
    if ui.tab == 0 and ui.stat.queue==0 and ui.stat.interp_state == linuxcnc.INTERP_IDLE:
      ui.axis = 1
      ui.joint = 1
      ui.jogging = True
      ui.hal.jog_active(True)
      ui.hal.set_axis(ui.axis)
      cmds.step_jog(ui, 1.)
  elif cmd == curses.KEY_DOWN: # jog Y-
    if ui.tab == 0 and ui.stat.queue==0 and ui.stat.interp_state == linuxcnc.INTERP_IDLE:
      ui.axis = 1
      ui.joint = 1
      ui.jogging = True
      ui.hal.jog_active(True)
      ui.hal.set_axis(ui.axis)
      cmds.step_jog(ui, -1.)
  elif cmd == curses.KEY_LEFT: # jog X-
    if ui.tab == 0 and ui.stat.queue==0 and ui.stat.interp_state == linuxcnc.INTERP_IDLE:
      ui.axis = 0
      ui.joint = 0
      ui.jogging = True
      ui.hal.jog_active(True)
      ui.hal.set_axis(ui.axis)
      cmds.step_jog(ui, -1.)
  elif cmd == curses.KEY_RIGHT: # jog X+
    if ui.tab == 0 and ui.stat.queue==0 and ui.stat.interp_state == linuxcnc.INTERP_IDLE:
      ui.axis = 0
      ui.joint = 0
      ui.jogging = True
      ui.hal.jog_active(True)
      ui.hal.set_axis(ui.axis)
      cmds.step_jog(ui, 1.)
  elif cmd == curses.KEY_NPAGE: # page down - jog Z or next screen
    if ui.tab == 1: # scroll program
      ui.program_start += 23
      if ui.program_start > len(ui.listing):
        ui.program_start = len(ui.listing)
    elif ui.tab == 3: # scroll tool list
      ui.tool_start += 60
      if ui.tool_start > len(ui.tools)-1:
        ui.tool_start = len(ui.tools)-1
    else:
      if ui.stat.queue==0 and ui.stat.interp_state == linuxcnc.INTERP_IDLE:
        ui.axis = 2
        ui.joint = 2
        ui.jogging = True
        ui.hal.jog_active(True)
        ui.hal.set_axis(ui.axis)
        cmds.step_jog(ui, -1.)
  elif cmd == curses.KEY_PPAGE: # page up - jog Z or previous screen
    if ui.tab == 1: # scroll program
      ui.program_start -= 23
      if ui.program_start < 0:
        ui.program_start = 0
    elif ui.tab == 3: # scroll tool list
      ui.tool_start -= 60
      if ui.tool_start < 0:
        ui.tool_start = 0
    else:
      if ui.stat.queue==0 and ui.stat.interp_state == linuxcnc.INTERP_IDLE:
        ui.axis = 2
        ui.joint = 2
        ui.jogging = True
        ui.hal.jog_active(True)
        ui.hal.set_axis(ui.axis)
        cmds.step_jog(ui, 1.)
  #elif cmd == curses.KEY_HOME: 
  #elif cmd == curses.KEY_END:
  #elif cmd == curses.KEY_F1: # all motion max velocity override?
  #elif cmd == curses.KEY_F2:
  #elif cmd == curses.KEY_F3:
  #elif cmd == curses.KEY_F4:
  #elif cmd == curses.KEY_F5:
  #elif cmd == curses.KEY_F6:
  #elif cmd == curses.KEY_F7:
  #elif cmd == curses.KEY_F8:
  #elif cmd == curses.KEY_F9:
  #elif cmd == curses.KEY_F10:
  else: # normal ASCII, handle below
    cmd = string.upper(chr(cmd & 0xFF))	# convert to single uppercase char for testing
    if cmd == 'A': # select A axis
      if ui.axis == 3 or ui.joint == 3:
        ui.axis = None
	ui.joint = None
      else:
	ui.axis = 3
	ui.joint = 3 # 4th axis active
      cmds.joint(ui)
    elif cmd == 'B': # block delete toggle
      cmds.block_delete(ui)
    elif cmd == 'C': # automatic mode
      cmds.automatic(ui)
    elif cmd == 'D': # MDI mode
      cmds.mdi_mode(ui)
    elif cmd == 'E': # toggle E Stop status
      cmds.estop_reset(ui)
    elif cmd == 'F': # toggle flood coolant
      cmds.flood(ui)
    elif cmd == 'G': # spindle stop
      cmds.spindle(ui, 0)
    elif cmd == 'H': # toggle optional stop
      cmds.opt_stop(ui)
    elif cmd == 'I': # toggle mist coolant
      cmds.mist(ui)
    elif cmd == 'J': # last screen
      prev_screen();
    elif cmd == 'K': # next screen
      next_screen()
    elif cmd == 'L': # reload tool table
      ui.display_msg("Reload tool table, find all entries....")
      cls()
      cmds.load_tool_table(ui)
      parse_tools()
      ui.display_msg("Done.")
      cls()
    elif cmd == 'M': # Manual mode
      cmds.manual(ui)
    elif cmd == 'N': # set coordinate offset
      set_coord_offset()
    elif cmd == 'O': # Open program file, via dialog
      open_program()
    elif cmd == 'P': # pause executing program
      cmds.pause(ui)
    elif cmd == 'Q': # quit the whole thing
      sys.exit(1) # note that this raises a SystemExit exception
    elif cmd == 'R': # run currently loaded program file
      cmds.run(ui)
    elif cmd == 'S': # reset interpreter
      cmds.reset(ui)
    elif cmd == 'T': # toggle machine on
      cmds.machine(ui)
    elif cmd == 'U': # cycle through jog steps
      cycle_jog_steps()
    elif cmd == 'V': # spindle reverse
      cmds.spindle(ui, -1)
    elif cmd == 'W': # spindle forward
      cmds.spindle(ui, 1)
    elif cmd == 'X': # select axis X
      if ui.axis == 0 or ui.joint == 0:
        ui.axis = None
	ui.joint = None
      else:
	ui.axis = 0
	ui.joint = 0
      cmds.joint(ui)
    elif cmd == 'Y': # select axis Y
      if ui.axis == 1 or ui.joint == 1:
        ui.axis = None
	ui.joint = None
      else:
	ui.axis = 1
	ui.joint = 1
      cmds.joint(ui)
    elif cmd == 'Z': # select axis Z
      if ui.axis == 2 or ui.joint == 2:
        ui.axis = None
	ui.joint = None
      else:
	ui.axis = 2
	ui.joint = 2
      cmds.joint(ui)
    elif cmd == '\'': # MDI command via dialog box
      mdi_input()
    elif cmd == '|' or cmd == '': # force a refresh
      ui.s.clear()
      ui.s.redrawwin()
      cls()
    elif cmd == '\\' or cmd == '': # override limits toggle
      cmds.override_lims(ui)
    elif cmd == '^' or cmd == curses.KEY_HOME: # home selected axis
      cmds.home_axis(ui)
    elif cmd == '*' or cmd == curses.KEY_END: # home all
      cmds.home_all(ui)
    elif cmd == '0': # Feed rate to 100%
      cmds.feedrate(ui, 100)
    elif cmd == '1': # Feed rate to 10%
      cmds.feedrate(ui, 10)
    elif cmd == '2': # Feed rate to 20%
      cmds.feedrate(ui, 20)
    elif cmd == '3': # Feed rate to 30%
      cmds.feedrate(ui, 30)
    elif cmd == '4': # Feed rate to 40%
      cmds.feedrate(ui, 40)
    elif cmd == '5': # Feed rate to 50%
      cmds.feedrate(ui, 50)
    elif cmd == '6': # Feed rate to 60%
      cmds.feedrate(ui, 60)
    elif cmd == '7': # Feed rate to 70%
      cmds.feedrate(ui, 70)
    elif cmd == '8': # Feed rate to 80%
      cmds.feedrate(ui, 80)
    elif cmd == '9': # Feed rate to 90%
      cmds.feedrate(ui, 90)
    elif cmd == '=' or cmd == '+': # spindle increase
      cmds.spindle_plus(ui)
    elif cmd == '-': # spindle decrease
      cmds.spindle_minus(ui)
  return



#
# Cycle through jog step increments
def cycle_jog_steps():
  global ui

  ui.jog_index = (ui.jog_index + 1)%len(ui.jog_increments)
  if ui.g20:
    cf=1.0; # jog step in inches
  else:
    cf=25.4; # convert jog increments to mm
  ui.jog_step = ui.jog_increments[ui.jog_index] * cf
  ui.hal.jog_step(ui.jog_step)

#
# Redraw the whole screen
def cls():
  global ui

  ui.s.erase()
  ui.stat.poll() # update linuxcnc status

  # screen tabs, reverse video for active tab
  offset = 0;
  for i in range(len(ui.tabs)):
    if i == ui.tab:
      ui.s.addstr(0,offset, ui.tabs[i], curses.A_REVERSE)
    else:
      ui.s.addstr(0,offset, ui.tabs[i])
    offset = offset + int(80/len(ui.tabs))

  # update coordinates for computing velocity, even if not on default tab
  now = time.time()
  dt = now - ui.time; # difference in s since last update
  ui.time = now
  for i in range(ui.n_axes):
    ui.v[i] = (ui.stat.position[i] - ui.old[i])/dt
    ui.old[i] = ui.stat.position[i]

  # draw rest of screen based on current tab
  if ui.tab == 0:
    default_tab()
  elif ui.tab == 1:
    g_code_tab()
  elif ui.tab == 2:
    status_tab()
  elif ui.tab == 3:
    tool_tab()
  else:
    default_tab()

  # line 23 - errors, messages from NML queue
  # check for messages back from linuxcnc, like errors or messages from
  # g code comments
  error = ui.err.poll()
  if error:
    ui.olderr = error; ui.dispcount = 0
  kind, text = ui.olderr
  if kind in (linuxcnc.NML_ERROR, linuxcnc.OPERATOR_ERROR):
    ui.s.addstr(23,0,"%s"%text[0:75], curses.A_REVERSE) # errors in reverse video
  else:
    ui.s.addstr(23,0,"%s"%text[0:75])
  ui.dispcount = ui.dispcount+1
  if ui.dispcount > 100: # clear old errors after 100 refresh cycles
    ui.dispcount = 0
    ui.olderr = (None, "")
  # done with default screen draw
  ui.s.refresh()

####
## Screen drawing functions
#
## Default tab - most info for actually running
def default_tab():
  global ui
  
  # compute unit conversion factor for machine units to g-code display units
  ucf = 1.0 # unit conversion factor, from inch to mm or reverse
  if ui.g20: # g code currently inches
    if ui.inch: # machine in inches
      ucf = 1.0 # do nothing, match
    else: # machine in mm
      ucf = 0.039370 # convert machine mm to g-code inch
  else: # g code currently mm
    if ui.inch: # machine in inches
      ucf = 25.4 # convert machine inches to g-code mm
    else: # machine in mm
      ucf = 1.0 # do nothing, match

  # lines 2 to 6, units, axis positions, overrides, jog step, states
  if ui.g20:
    ui.s.addstr(1,9, "units are inches")
  else:
    ui.s.addstr(1,9, "units are mm")
  # create relative units from machine units; not stored in ui.stat, so compute
  # each update!
  positions = [(i-j) for i, j in zip(ui.stat.actual_position, ui.stat.tool_offset)]
  positions = [(i-j) for i, j in zip(positions, ui.stat.g5x_offset)]
  if ui.stat.rotation_xy != 0: # rotate coords around Z
    t = math.radians(-ui.stat.rotation_xy)
    x = positions[0]; y = positions[1];
    positions[0] = x*math.cos(t) - y*math.sin(t)
    positions[1] = x*math.sin(t) + y*math.cos(t)
  positions = [(i-j) for i, j in zip(positions, ui.stat.g92_offset)]
  ui.s.addstr(1,30, "E - Enabled, H - Homed, A - Active Jog")
  ui.s.addstr(2,0, "Relative     Absolute     DTG    EHA   Velocity")
  for i in range(ui.n_axes):
    e = "*" if ui.stat.joint[i]["enabled"] else " "
    h = "*" if ui.stat.joint[i]["homed"] else " "
    f = "*" if ui.axis == i else " "
    ui.s.addstr(3+i,0, "%s%+9.4f  %+9.4f  %+9.4f %s%s%s %+9.4f"% \
      (ui.axis_ltrs[i], positions[i]*ucf, ui.stat.actual_position[i]*ucf, ui.stat.dtg[i]*ucf, e, h, f, ui.v[i]*ucf*60.0))

  # overrides, states, jog step, etc.
  e = "*" if ui.stat.feed_override_enabled else " "
  ui.s.addstr(2,53,"Feed    %3d%% %s"%(int(ui.stat.feedrate*100), e))
  e = "*" if ui.stat.spindle[0]["override_enabled"] else " "
  ui.s.addstr(3,53,"Spindle %3d%% %s"%(int(ui.stat.spindle[0]["override"]*100), e))
  max_v = ui.stat.max_velocity
  if max_v > 1000:
    ui.s.addstr(4,53,"Max Vel ******") # indicate max velocity not set
  else:
    ui.s.addstr(4,53,"Max Vel %6.2f"%(max_v*60.0)) # unit/sec to unit/min
  # jogging step size, * if on
  jog_on = "*" if ui.jogging else " "
  ui.s.addstr(5,53,"Jog Step %6.4f %s"%(ui.jog_step, jog_on))
  # machine status, operating mode
  estop = "E-STOP" if ui.stat.estop else "ENABLE"
  machine = "ON " if ui.stat.task_state == linuxcnc.STATE_ON else "OFF"
  if ui.stat.task_mode == linuxcnc.MODE_MDI:
    mode = " MDI  "
  elif ui.stat.task_mode == linuxcnc.MODE_AUTO:
    mode = " AUTO "
  elif ui.stat.task_mode == linuxcnc.MODE_MANUAL:
    mode = "MANUAL"
  else:
    mode = "UNKNWN"
  hold = "HOLD" if ui.stat.feed_hold_enabled else " RUN"
  ui.s.addstr(6,48,"%s | %s | %s | %s"%(estop, machine, mode, hold))

  # line 7 - motion mode, delay timer
  if ui.stat.motion_mode == linuxcnc.TRAJ_MODE_COORD:
    traj = " coord"
  elif ui.stat.motion_mode == linuxcnc.TRAJ_MODE_FREE:
    traj = " joint"
  elif ui.stat.motion_mode == linuxcnc.TRAJ_MODE_TELEOP:
    traj = "teleop"
  else:
    traj = "unknwn"
  ui.s.addstr(7,50,"Motion mode %s"%traj)
  if ui.stat.delay_left > 0:
    ui.s.addstr(7,40, "Delaying; %6.3f secs left")

  # line 8,9 - Current coord system, offsets, G92 offsets, Opt Stop, Block Delete
  optstop = "*" if ui.stat.optional_stop else " "
  blockdel = "*" if ui.stat.block_delete else " "
  ui.s.addstr(8,66, "Opt Stop %s"%optstop)
  ui.s.addstr(9,66, "Blk Del  %s"%blockdel)

  if ui.stat.g5x_index > 5:
    cs = "G59.%1d"%(ui.stat.g5x_index-6)
  else:
    cs = "G5%1d"%(3+ui.stat.g5x_index)
  ui.s.addstr(8,0, "%s X% 9.4f Y% 9.4f Z% 9.4f A% 9.4f R%+10.5f"%(cs, ui.stat.g5x_offset[0], ui.stat.g5x_offset[1], ui.stat.g5x_offset[2], ui.stat.g5x_offset[3], ui.stat.rotation_xy))
  ui.s.addstr(9,0, "G92 X% 9.4f Y% 9.4f Z% 9.4f A% 9.4f"%(ui.stat.g92_offset[0], ui.stat.g92_offset[1], ui.stat.g92_offset[2], ui.stat.g92_offset[3]))

  # line 10 - blank

  # line 11 - Spindle enable, speed; feed rate; spindle dir; coolant toggles
  spindle = "OFF"
  if ui.stat.spindle[0]["direction"] == 1: spindle = "FWD"
  if ui.stat.spindle[0]["direction"] == -1: spindle = "REV"
  flood = " ON" if ui.stat.flood == linuxcnc.FLOOD_ON else "OFF"
  mist = " ON" if ui.stat.mist == linuxcnc.MIST_ON else "OFF"
  ui.s.addstr(11,0,"S%5d  F%9.4f  Spindle %s  Flood %s  Mist %s"%\
    (int(ui.stat.spindle[0]["speed"]), ui.stat.settings[1], \
     spindle, flood, mist))

  # line 12 - blank

  # line 13 - M codes
  mcodes = ""
  for i in ui.stat.mcodes[1:]:
    if i == -1: continue
    mcodes += "M%d " % i
  ui.s.addstr(13,0, mcodes)

  # line 14 - G codes
  gcodes = ""
  for i in ui.stat.gcodes[1:]:
    if i == -1: continue
    if i % 10 == 0:
	    gcodes += "G%d " % (i/10)
    else:
	    gcodes += "G%d.%d " % (i/10, i%10)
  ui.s.addstr(14,0, gcodes)

  # line 15  - blank

  # line 16 - tool in spndle, tool prepped for change, current tool z, dia
  ui.s.addstr(16,0,"Tool in spindle %3d     next %3d     z offset %8.4f diameter %8.6f"%\
    (ui.stat.tool_in_spindle, ui.stat.pocket_prepped, ui.stat.tool_table[0].zoffset, \
     ui.stat.tool_table[0].diameter))

  # line 17 - blank

  # line 18 - current program filename
  if ui.stat.file:
    ui.s.addstr(18,0,"%s"%(ui.stat.file))
  else:
    ui.s.addstr(18,0,"No progam file loaded...")

  # line 19 - currently executing command
  if ui.stat.command:
    if len(ui.stat.command) > ui.maxx-19:
      cmd = ui.stat.command[0:ui.maxx-19]
    else:
      cmd = ui.stat.command
  else:
    cmd = "no current command"
  ui.s.addstr(19,0,"M:%6d L:%6d %s"%(ui.stat.motion_line, ui.stat.current_line, cmd))

  # line 20 - blank

  # line 21 - Limit Override warning
  if ui.limits_override:
    ui.s.addstr(21,0,"****LIMITS OVERRIDDEN****")

  # line 22 - blank
  



## Current Program Listing
def g_code_tab():
  global ui
  # print the current program
  s = ui.program_start
  l = ui.stat.current_line
  w = ui.maxx - 7
  if ui.listing:
    n = len(ui.listing)
    p = l/n*100
    start = -1*(ui.maxx-34)
    ui.s.addstr(1,0, "CURRENT FILE: %s  %d of %d lines (%%%3d)"%(ui.stat.file[start:-1], l, n, p))
    # list from start offset to end of screen
    for i in range(2,23):
      n = s-2+i
      if n<0: n=0
      if n>len(ui.listing)-1: break;
      if n==l:
        ui.s.addstr(i,0, "%6d %s"%(n+1, ui.listing[n][0:w]), curses.A_REVERSE)
      else:
        ui.s.addstr(i,0, "%6d %s"%(n+1, ui.listing[n][0:w]))
  else:
    ui.s.addstr(1,0, "NO CURRENT FILE")

  # done with g code screen



## Status tab - lots of parameter info, hal pins
def status_tab():
  global ui
  # long listing of current status
  # hal.get_value() currently not implemented in the halmodule of machinekit
  # once that is fixed, can use all these lines for real-time readout of info.
  # SIGH.
#  ui.s.addstr(1,0, "adapt-fd:%8.6f coord-err:%1d coord-mode:%1d teleop:%1d feed-hold:%1d feed-inhibit:%1d"%(\
#    hal.get_value("motion.adaptive-feed"), \
#    hal.get_value("motion.coord-error"), \
#    hal.get_value("motion.coord-mode"), \
#    hal.get_value("motion.teleop-mode"), \
#    hal.get_value("motion.feed-hold"), \
#    hal.get_value("motion.feed-inhibit") \
#    ))
#  ui.s.addstr(2,0, "in-pos:%1d enabled:%1d soft-limit:%1d servo.period:%9u current-vel:%9.6f"%(\
#    hal.get_value("motion.in-position"), \
#    hal.get_value("motion.motion-enabled"), \
#    hal.get_value("motion.on-soft-limit"), \
#    hal.get_value("motion.servo.last-period"), \
#    hal.get_value("motion.current-vel") \
#    ))
#  ui.s.addstr(3,0, "probe:%1d spindle- on:%1d fwd:%1d rev:%1d at-speed:%1d brake:%1d"%(\
#    hal.get_value("motion.probe-input"), \
#    hal.get_value("motion.spindle-on"), \
#    hal.get_value("motion.spindle-forward"), \
#    hal.get_value("motion.spindle-reverse"), \
#    hal.get_value("motion.spindle-at-speed"), \
#    hal.get_value("motion.spindle-brake") \
#    ))
#  ui.s.addstr(4,0, "digital in- 00:%1d 01:%1d 02:%1d 03:%1d   out- 00:%1d 01:%1d 02:%1d 03:%1d"%(\
#    hal.get_value("motion.digital-in-00"), \
#    hal.get_value("motion.digital-in-01"), \
#    hal.get_value("motion.digital-in-02"), \
#    hal.get_value("motion.digital-in-03"), \
#    hal.get_value("motion.digital-out-00"), \
#    hal.get_value("motion.digital-out-01"), \
#    hal.get_value("motion.digital-out-02"), \
#    hal.get_value("motion.digital-out-03") \
#    ))
#  ui.s.addstr(5,0, "analog  in- 00:%9.6f 01:%9.6f 02:%9.6f 03:%9.6f"%(\
#    hal.get_value("motion.analog-in-00"), \
#    hal.get_value("motion.analog-in-01"), \
#    hal.get_value("motion.analog-in-02"), \
#    hal.get_value("motion.analog-in-03") \
#    ))
#  ui.s.addstr(6,0, "analog out- 00:%9.6f 01:%9.6f 02:%9.6f 03:%9.6f"%(\
#    hal.get_value("motion.analog-out-00"), \
#    hal.get_value("motion.analog-out-01"), \
#    hal.get_value("motion.analog-out-02"), \
#    hal.get_value("motion.analog-out-03") \
#    ))
#  
#  ui.s.addstr(8,0, "X active:%1d a-enable:%1d a-fault:%1d err:%1d flt:%1d h-sw:%1d homed:%1d homing:%1d in-pos:%1d"% (\
#    hal.get_value("axis.0.active"), \
#    hal.get_value("axis.0.amp-enable-out"), \
#    hal.get_value("axis.0.amp-fault-in"), \
#    hal.get_value("axis.0.error"), \
#    hal.get_value("axis.0.faulted"), \
#    hal.get_value("axis.0.home-sw-in"), \
#    hal.get_value("axis.0.homed"), \
#    hal.get_value("axis.0.homing"), \
#    hal.get_value("axis.0.in-position") \
#    ))
#  ui.s.addstr(9,0, "X jog- enable:1 vel-mode:1 kb-jog:1 cnts:%9d scale:%9.6f pos:%9.6f"% (\
#    hal.get_value("axis.0.jog-enable"), \
#    hal.get_value("axis.0.jog-vel-mode"), \
#    hal.get_value("axis.0.kb-jog-active"), \
#    hal.get_value("axis.0.jog-counts"), \
#    hal.get_value("axis.0.jog-scale"), \
#    hal.get_value("axis.0.jog-cmd-pos") \
#    ))
#  ui.s.addstr(10,0, "X backlash- corr:%9.6f filt:%9.6f vel:%9.6f"% (\
#    hal.get_value("axis.0.backlash-corr"), \
#    hal.get_value("axis.0.backlash-filt"), \
#    hal.get_value("axis.0.backlash-vel") \
#    ))
#  ui.s.addstr(11,0, "X motor- pos:%9.6f fb:%9.6f limits hard -%1d/+%1d switch -%1d/+%1d wheel:%1d"% (\
#    hal.get_value("axis.0.motor-pos-cmd"), \
#    hal.get_value("axis.0.motor-pos-fb"), \
#    hal.get_value("axis.0.neg-hard-limit"), \
#    hal.get_value("axis.0.pos-hard-limit"), \
#    hal.get_value("axis.0.neg-lim-sw-in"), \
#    hal.get_value("axis.0.pos-lim-sw-in"), \
#    hal.get_value("axis.0.wheel-jog-active") \
#    ))
#  ui.s.addstr(12,0, "Y active:%1d a-enable:%1d a-fault:%1d err:%1d flt:%1d h-sw:%1d homed:%1d homing:%1d in-pos:%1d"% (\
#    hal.get_value("axis.1.active"), \
#    hal.get_value("axis.1.amp-enable-out"), \
#    hal.get_value("axis.1.amp-fault-in"), \
#    hal.get_value("axis.1.error"), \
#    hal.get_value("axis.1.faulted"), \
#    hal.get_value("axis.1.home-sw-in"), \
#    hal.get_value("axis.1.homed"), \
#    hal.get_value("axis.1.homing"), \
#    hal.get_value("axis.1.in-position") \
#    ))
#  ui.s.addstr(13,0, "Y jog- enable:1 vel-mode:1 kb-jog:1 cnts:%9d scale:%9.6f pos:%9.6f"% (\
#    hal.get_value("axis.1.jog-enable"), \
#    hal.get_value("axis.1.jog-vel-mode"), \
#    hal.get_value("axis.1.kb-jog-active"), \
#    hal.get_value("axis.1.jog-counts"), \
#    hal.get_value("axis.1.jog-scale"), \
#    hal.get_value("axis.1.jog-cmd-pos") \
#    ))
#  ui.s.addstr(14,0, "Y backlash- corr:%9.6f filt:%9.6f vel:%9.6f"% (\
#    hal.get_value("axis.1.backlash-corr"), \
#    hal.get_value("axis.1.backlash-filt"), \
#    hal.get_value("axis.1.backlash-vel") \
#    ))
#  ui.s.addstr(15,0, "Y motor- pos:%9.6f fb:%9.6f limits hard -%1d/+%1d switch -%1d/+%1d wheel:%1d"% (\
#    hal.get_value("axis.1.motor-pos-cmd"), \
#    hal.get_value("axis.1.motor-pos-fb"), \
#    hal.get_value("axis.1.neg-hard-limit"), \
#    hal.get_value("axis.1.pos-hard-limit"), \
#    hal.get_value("axis.1.neg-lim-sw-in"), \
#    hal.get_value("axis.1.pos-lim-sw-in"), \
#    hal.get_value("axis.1.wheel-jog-active") \
#    ))
#  ui.s.addstr(16,0, "Z active:%1d a-enable:%1d a-fault:%1d err:%1d flt:%1d h-sw:%1d homed:%1d homing:%1d in-pos:%1d"% (\
#    hal.get_value("axis.2.active"), \
#    hal.get_value("axis.2.amp-enable-out"), \
#    hal.get_value("axis.2.amp-fault-in"), \
#    hal.get_value("axis.2.error"), \
#    hal.get_value("axis.2.faulted"), \
#    hal.get_value("axis.2.home-sw-in"), \
#    hal.get_value("axis.2.homed"), \
#    hal.get_value("axis.2.homing"), \
#    hal.get_value("axis.2.in-position") \
#    ))
#  ui.s.addstr(17,0, "Z jog- enable:1 vel-mode:1 kb-jog:1 cnts:%9d scale:%9.6f pos:%9.6f"% (\
#    hal.get_value("axis.2.jog-enable"), \
#    hal.get_value("axis.2.jog-vel-mode"), \
#    hal.get_value("axis.2.kb-jog-active"), \
#    hal.get_value("axis.2.jog-counts"), \
#    hal.get_value("axis.2.jog-scale"), \
#    hal.get_value("axis.2.jog-cmd-pos") \
#    ))
#  ui.s.addstr(18,0, "Z backlash- corr:%9.6f filt:%9.6f vel:%9.6f"% (\
#    hal.get_value("axis.2.backlash-corr"), \
#    hal.get_value("axis.2.backlash-filt"), \
#    hal.get_value("axis.2.backlash-vel") \
#    ))
#  ui.s.addstr(19,0, "Z motor- pos:%9.6f fb:%9.6f limits hard -%1d/+%1d switch -%1d/+%1d wheel:%1d"% (\
#    hal.get_value("axis.2.motor-pos-cmd"), \
#    hal.get_value("axis.2.motor-pos-fb"), \
#    hal.get_value("axis.2.neg-hard-limit"), \
#    hal.get_value("axis.2.pos-hard-limit"), \
#    hal.get_value("axis.2.neg-lim-sw-in"), \
#    hal.get_value("axis.2.pos-lim-sw-in"), \
#    hal.get_value("axis.2.wheel-jog-active") \
#    ))
#  ui.s.addstr(20,0, "A active:%1d a-enable:%1d a-fault:%1d err:%1d flt:%1d h-sw:%1d homed:%1d homing:%1d in-pos:%1d"% (\
#    hal.get_value("axis.2.active"), \
#    hal.get_value("axis.2.amp-enable-out"), \
#    hal.get_value("axis.2.amp-fault-in"), \
#    hal.get_value("axis.2.error"), \
#    hal.get_value("axis.2.faulted"), \
#    hal.get_value("axis.2.home-sw-in"), \
#    hal.get_value("axis.2.homed"), \
#    hal.get_value("axis.2.homing"), \
#    hal.get_value("axis.2.in-position") \
#    ))
#  ui.s.addstr(21,0, "A jog- enable:1 vel-mode:1 kb-jog:1 cnts:%9d scale:%9.6f pos:%9.6f"% (\
#    hal.get_value("axis.3.jog-enable"), \
#    hal.get_value("axis.3.jog-vel-mode"), \
#    hal.get_value("axis.3.kb-jog-active"), \
#    hal.get_value("axis.3.jog-counts"), \
#    hal.get_value("axis.3.jog-scale"), \
#    hal.get_value("axis.3.jog-cmd-pos") \
#    ))
#  ui.s.addstr(22,0, "A backlash- corr:%9.6f filt:%9.6f vel:%9.6f"% (\
#    hal.get_value("axis.3.backlash-corr"), \
#    hal.get_value("axis.3.backlash-filt"), \
#    hal.get_value("axis.3.backlash-vel") \
#    ))
#  ui.s.addstr(23,0, "A motor- pos:%9.6f fb:%9.6f limits hard -%1d/+%1d switch -%1d/+%1d wheel:%1d"% (\
#    hal.get_value("axis.3.motor-pos-cmd"), \
#    hal.get_value("axis.3.motor-pos-fb"), \
#    hal.get_value("axis.3.neg-hard-limit"), \
#    hal.get_value("axis.3.pos-hard-limit"), \
#    hal.get_value("axis.3.neg-lim-sw-in"), \
#    hal.get_value("axis.3.pos-lim-sw-in"), \
#    hal.get_value("axis.3.wheel-jog-active") \
#    ))
  ui.s.addstr(1,0,"hal.get_value not implemented; no status info.")
  # done with status screen



## Tool tab - list the tool table
def tool_tab():
  global ui
  row = 1; cols = [0,28,54];
  for i in range(len(cols)):
    ui.s.addstr(1,cols[i], " #  Diameter  Length")
  if len(ui.tools)<1:
    ui.s.addstr(2,0, "No tools in tool table!")
    return
  N = len(ui.tools)-ui.tool_start
  if N<0:
    ui.tool_start -= 60
    if ui.tool_start < 0: ui.tool_start=0
    N = len(ui.tools)-ui.tool_start
  if N>66: N=66
  for i in range(N):
    tool = ui.stat.tool_table[ui.tools[i+ui.tool_start]]
    j = i%3
    if j == 0: row += 1
    ui.s.addstr(row,cols[j], "%3d %8.4f %8.4f"%(tool.id, tool.diameter, tool.zoffset))

  # done with tool table

###
## dialog commands
#

# get program file name in dialog, then open it
def open_program():
  global ui

  # create a dialog
  dialog = curses_dialog.DialogListBox(2,2, ui.maxy-2,ui.maxx-2, "Open Program File")
  buttons = ["Open", "Cancel"]

  cwd = os.getcwd()
  while(1): # loop until cancel or file chosen
    # fetch the file list
    rawlist = os.listdir(cwd)
    files = []
    for f in rawlist:
      if f[0] != ".":
        files.append(f)
    files.append("..")
    files.sort()

    (b, idx) = dialog.show(buttons, files)
    if b == -1 or b == 1: # escape or cancel
      return

    # if name is a directory, fetch listing and restart
    name = os.path.join(cwd, files[idx])
    if os.path.isdir(name): # directory, so keep going
      cwd = name
      continue # next round

    else: # file, so open it for read
      try:
        # send the command
        ui.cmd.program_open(name)
        # read file into buffer for display
        # someday, this may be a problem if the file is larger than available RAM....
        f = open(name, "rt")
        ui.listing = f.readlines()
        f.close()
      except IOError: # trap IOError so we don't die from mistyped filename
        ui.error_msg("Error opening file %s"%name)
        ui.listing = None
      return # terminate while loop
  # end while loop


# set X,Y,Z,A offset directly
def set_coord_offset():
  global ui

  prompt = "Enter axis letter, then new value for axis position; e.g. \n\
    X4.5 sets the current X position to 4.5. May set X, Y, Z, A in one\n \
    line.\nSet:"
  y = ((ui.maxy - 4)/2)-2
  x = ((ui.maxx - 4)/2)-35
  dialog = curses_dialog.DialogEntryBox(y,x, 6,70, "Set Current Position")
  buttons = ["Set", "Cancel"]
  (b, string) = dialog.show(buttons, prompt)

  if b == -1 or b == 1:
    # escape or cancel
    return

  if not string or string == '\n':
    return

  # send the command
  cmds.set_coordinates(ui, string)


# parse tool table
# linuxcnc has a fixed 1001-entry tool table, which is initialized
# to have ids of -1, offsets of 0. So, need to scan tool table for ids != -1
# tool entry 0 is what is in the spindle, so skip
def parse_tools():
  global ui

  ui.tools = []
  for i in range(1,len(ui.stat.tool_table)):
    if ui.stat.tool_table[i].id != -1:
      ui.tools.append(i) # store index for tool listing

# get line of input for mdi command
def mdi_input():
  global ui

  # create a dialog
  prompt = "MDI:"
  y = ((ui.maxy - 4)/2)-2
  x = ((ui.maxx - 4)/2)-35
  dialog = curses_dialog.DialogEntryBox(y,x, 4,70, "MDI Command")
  buttons = ["Send", "Cancel"]
  (b, mdi) = dialog.show(buttons, prompt)

  if b == -1 or b == 1:
    # escape or cancel
    return

  if not mdi or mdi == '\n':
    return

  # send the command
  cmds.mdi(ui, mdi)




# swap screen to previous in list, wrapping at beginning
def prev_screen():
  global ui
  ui.tab = ui.tab - 1
  if ui.tab < 0:
    ui.tab = len(ui.tabs)-1;
  ui.s.clear()
  ui.s.redrawwin()
  cls()

# swap screen to next in list, wrapping at end
def next_screen():
  global ui
  ui.tab = ui.tab + 1
  if ui.tab > len(ui.tabs)-1:
    ui.tab = 0;
  ui.s.clear()
  ui.s.redrawwin()
  cls()




#################################
#####   END FUNCTION CODE   #####
#################################

# Start it up!
try:
  main()

# sys.exit raises an exception!
except SystemExit:
  try:
    curses.endwin()
  except:
    pass
  sys.stderr.write("CNCTERM: Clean shutdown.\n")
  rc = 0

# Whoops!  we fell down and went boom.
except:
  (exctype, excval, exctb) = sys.exc_info()
  try:
    curses.endwin()
  except:
    pass
  sys.stderr.write("CNCTERM: FATAL ERROR: Unhandled exception!  Shut it down!\n")
  sys.stderr.write("\n")
  traceback.print_exception(exctype, excval, exctb)
  rc = 1

sys.exit(rc)
