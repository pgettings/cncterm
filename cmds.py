#!/usr/bin/python2
#
# LinuxCNC interface for serial terminals
#
# (C) 2022 P Gettings
# 
# See LinuxCNC for licensing terms.
#

#
# Curses-based client for linuxcnc
#
# Command functions, talking to ui.c (linuxcnc.command)
#

### FIXME - check linuxcnc modes before executing commands.

# built-in modules
import sys, string, time
import traceback
import curses

# program specific modules
import linuxcnc
import gcode

# ABORT ABORT ABORT!!!
def abort(ui):
  ui.cmd.abort()

# toggle block delete
def block_delete(ui):
  if ui.stat.block_delete:
    ui.cmd.set_block_delete(0) # 0 is off?
  else:
    ui.cmd.set_block_delete(1)

# home currently selected axis
def home_axis(ui):
  if ui.axis == None:
    ui.error_msg("No axis selected for homing!")
    return
  else:
    ui.cmd.home(ui.axis)

# home all axes
def home_all(ui):
  ui.cmd.home(-1)

# enable joint/axis
# enable teleop mode if homed, free mode if not
# if ui.axis or ui.joint == None, then no
# axes enabled for jogging or homing!!!
def joint(ui):
  if ui.axis == None or ui.joint == None:
    ui.error_msg("No axis selected for motion!")
    return
  if ui.stat.homed.count(1) == ui.stat.joints: # all homed, enable teleop
    ui.cmd.teleop_enable(1); # axis jogging, not individual joints!
  else:
    ui.cmd.teleop_enable(0); # free/joint jogging, not axes!

# perform a single jog increment, multiplied by f (negative for reverse)
def step_jog(ui, f):
  if ui.axis == None or ui.joint == None:
    ui.error_msg("No axis selected for motion!")
    return
  joint(ui)
  if ui.stat.motion_mode == linuxcnc.TRAJ_MODE_TELEOP:
    ui.cmd.jog(linuxcnc.JOG_INCREMENT, True, ui.axis, ui.stat.max_velocity, ui.jog_step*f)
  else:
    ui.cmd.jog(linuxcnc.JOG_INCREMENT, False, ui.joint, ui.stat.max_velocity, ui.jog_step*f)

# start, stop, fwd, rev:
# flag = -1 ==> reverse
# flag =  0 ==> stop
# flag = +1 ==> forward
def spindle(ui, flag):
  if not ui.stat.task_mode == linuxcnc.MODE_MANUAL:
    ui.error_msg("Must be in MANUAL mode!")
    return
  if flag == 0:
    ui.cmd.spindle(linuxcnc.SPINDLE_OFF, 0, ui.spindle)
  elif flag == 1:
    ui.cmd.spindle(linuxcnc.SPINDLE_FORWARD, 1, ui.spindle)
  elif flag == -1:
    ui.cmd.spindle(linuxcnc.SPINDLE_REVERSE, 1, ui.spindle)
  else:
    ui.cmd.spindle(linuxcnc.SPINDLE_OFF, 0, ui.spindle)

def spindle_plus(ui):
  if not ui.stat.task_mode == linuxcnc.MODE_MANUAL:
    ui.error_msg("Must be in MANUAL mode!")
    return
  ui.cmd.spindle(linuxcnc.SPINDLE_INCREASE, ui.spindle)

def spindle_minus(ui):
  if not ui.stat.task_mode == linuxcnc.MODE_MANUAL:
    ui.error_msg("Must be in MANUAL mode!")
    return
  ui.cmd.spindle(linuxcnc.SPINDLE_DECREASE, ui.spindle)

# toggle machine on, off
def machine(ui):
  if ui.stat.task_state == linuxcnc.STATE_ON:
    ui.cmd.state(linuxcnc.STATE_OFF)
  else:
    ui.cmd.state(linuxcnc.STATE_ON)

# reset interpreter
def reset(ui):
  ui.cmd.reset_interpreter()

# run current program in auto mode
def run(ui):
  if not ui.stat.task_mode == linxucnc.MODE_AUTO:
    ui.error_msg("Must be in AUTO mode!")
    return
  #### FIXME
  # check if single block set, then STEP?
  ui.cmd.auto(linuxcnc.AUTO_RUN)

# pause running program
def pause(ui):
  if ui.stat.interp_state == linuxcnc.INTERP_PAUSED:
    ui.cmd.auto(linuxcnc.AUTO_RESUME)
  else:
    ui.cmd.auto(linuxcnc.AUTO_PAUSE)

# go to manual mode
def manual(ui):
  ui.cmd.mode(linuxcnc.MODE_MANUAL)
  ui.cmd.wait_complete() # wait until mode switch executed

# toggle mist coolant
def mist(ui):
  if ui.stat.mist == linuxcnc.MIST_ON:
    ui.cmd.mist(linuxcnc.MIST_OFF)
  else:
    ui.cmd.mist(linuxcnc.MIST_ON)

# toggle flood coolant
def flood(ui):
  if ui.stat.flood == linuxcnc.FLOOD_ON:
    ui.cmd.flood(linuxcnc.FLOOD_OFF)
  else:
    ui.cmd.flood(linuxcnc.FLOOD_ON)

def override_lims(ui):
  override = False;
  for i in range(len(ui.stat.joint)):
    if ui.stat.joint[i]["override_limits"]:
      override = True
  ui.cmd.override_limits();
  if override:
    ui.limits_override = False;
  else:
    ui.limits_override = True;

# reset estop
def estop_reset(ui):
  ui.cmd.state(linuxcnc.STATE_ESTOP_RESET)

# mdi mode
def mdi_mode(ui):
  if ok_for_mdi(ui.stat):
    ui.cmd.mode(linuxcnc.MODE_MDI)
    ui.cmd.wait_complete() # wait until mode switch executed
  else:
    ui.error_msg("Not ready for MDI input!")

# automatic mode
def automatic(ui):
  ui.cmd.mode(linuxcnc.MODE_AUTO)
  ui.cmd.wait_complete() # wait until mode switch executed

def mdi(ui, string):
  if ok_for_mdi(ui.stat):
    if ui.stat.task_mode != linuxcnc.MODE_MDI:
      ui.error_msg("Must be in MDI mode!")
      return
    ui.cmd.mdi(string)
  else:
    ui.error_msg("Not ready for MDI input!")

def load_tool_table(ui):
  # reload tool table; how to set new name or file?
  ui.cmd.load_tool_table()

# send an MDI command to reset current position offsets
def set_coordinates(ui, string):
  cmd="G10 L20 P0 %s"%string
  if ui.stat.task_mode != linuxcnc.MODE_MDI:
    ui.cmd.mode(linuxcnc.MODE_MDI)
    ui.cmd.wait_complete() # wait until mode switch executed
  ui.cmds.mdi(cmd)

def ok_for_mdi(s):
  return not s.estop and s.enabled and (s.homed.count(1) == s.joints) and (s.interp_state == linuxcnc.INTERP_IDLE)

# rate is feed rate percentage, 0-100+
def feedrate(ui, rate):
  ui.cmd.feedrate(rate/100.0) # scale to [0,1]
  ui.cmd.wait_complete() # wait until mode switch executed
