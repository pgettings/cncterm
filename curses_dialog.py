#
# This file is part of Amphion.
#
# Amphion is copyright (c) 2000 by
# Patrick Tullmann, Brian Loss, and Paul Gettings.
# All Rights Reserved.
# 
# Amphion is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License, version 2, as
# published by the Free Software Foundation.
# 
# Amphion is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details, in the file
# COPYING.  If that file is not present, write to the Free
# Software Foundation, 59 Temple Place - Suite 330, Boston, MA  
# 02111-1307, USA
# 
# The Amphion maintainers request that users forward any 
# improvements to <amphion-devel@lists.sourceforge.net>.
#

#
# curses-based dialog list box
#

import curses
import string

UP = 1
DOWN = 2
RIGHT = 3
LEFT = 4
SELECT = 5
ESCAPE = 6
PGUP = 7
PGDN = 8
HOME = 9
END = 10
BACKSPACE = 11

class DialogBox:
  def __init__(self, y,x, h,w, title):
    self.win = curses.newwin(h,w,y,x)
    self.title = title
    self.w = w-2
    self.h = h-2

  # buttons is tuple/list of button text
  # text is string to display - will be wrapped
  # default is default button, 0-indexed
  def show(self, buttons, text, default=0):
    # must have at least one button
    if not buttons:
      return -1
    # wrap the text at words
    lines = 0
    j = 0; k = -1; msg=[]
    for i in range(len(text)):
      j = j+1
      if text[i] == ' ':
	k = i
      if text[i] == '\n':
	j = 0; k = -1
	lines = lines+1
      if j > self.w:
	lines = lines+1
	j = 0
	# replace last space with \n
	if k == -1:
	  msg.insert(self.w-1,'\n')
	else:
	  if k == i:
	    msg.append(text[i])
	  msg.insert(k, '\n')
      else:
	msg.append(text[i])
    if lines > self.h:
      lines = self.h

    # draw the dialog
    self.win.clear()
    self.win.box()
    self.win.addstr(0,2, self.title[0:self.w-2], curses.A_STANDOUT)

    # draw the message
    #   parse msg for \n, incrementing lines
    y = 1; x = 1;
    for i in range(len(msg)):
      if msg[i] == '\n':
	y = y+1; x = 1
	if y > self.h:
	  break
      self.win.addch(y,x,ord(msg[i]), curses.A_NORMAL)
      x = x+1
    # draw the buttons
    #  compute # of buttons, then cut up lowest line, and center
    #  add <> around buttons
    #  selected button is standout, other normal
    current_b = default
    y = self.h
    nb = len(buttons)
    dx = self.w/nb
    x0 = dx/2
    # draw the buttons - assume they won't overlap
    for i in range(len(buttons)):
      if current_b == i:
	highlight = curses.A_STANDOUT
      else:
	highlight = curses.A_NORMAL
      offset = (len(buttons[i])+2) / 2
      self.win.addstr(y, ((i+1)*dx)-x0-offset, "<"+buttons[i]+">", highlight)

    self.win.box()
    self.win.addstr(0,2, self.title[0:self.w-2], curses.A_STANDOUT)

    # run our busy wait loop to get keyboard input
    while (1):
      c = self.get_cmd()
      if c == LEFT:
	current_b = current_b-1
	if current_b < 0:
	  current_b = len(buttons)-1
      elif c == RIGHT:
        current_b = current_b+1
        if current_b > len(buttons)-1:
	  current_b = 0
      elif c == SELECT:
	return current_b
      elif c == ESCAPE:
	return (-1, -1)

      # redraw the buttons
      self.win.move(y,1)
      self.win.clrtoeol()
      self.win.box()
      self.win.addstr(0,2, self.title[0:self.w-2], curses.A_STANDOUT)
      for i in range(len(buttons)):
	if current_b == i:
	  highlight = curses.A_STANDOUT
	else:
	  highlight = curses.A_NORMAL
	offset = (len(buttons[i])+2) / 2
	self.win.addstr(y, ((i+1)*dx)-x0-offset, "<"+buttons[i]+">", highlight)

  def get_cmd(self):
    # get a keystroke and parse into cmd codes
    self.win.nodelay(0)
    c = self.win.getch()
    if c == ord('\n'):
      # return = select
      return SELECT
    elif chr(c&0xff) == '[':
      return HOME;
    elif chr(c&0xff) == ']':
      return END;
    # test for printable ASCII
    elif c&0xff > 31 and c&0xff < 127:
      return 1000 + (c&0xff) - 32

    elif c == 127 or c == 8: # 127 is decimal for delete, 8 is decimal
      return BACKSPACE	     # for backspace
    elif c == 27: # 27 is decimal for ESC
      c = self.win.getch()
      if c == ord('['):
	# movement key
	c = self.win.getch()
	# up is A
	if c == ord('A'):
	  return UP
	# down is B
	if c == ord('B'):
	  return DOWN
	# right is C
	if c == ord('C'):
	  return RIGHT
	# left is D
	if c == ord('D'):
	  return LEFT
	# pgup is 5
	if c == ord('5'):
	  return PGUP
	# pgdn is 6
	if c == ord('6'):
	  return PGDN
      elif c == ord('O'):
	# Home/End
	c = self.win.getch()
	# home is H
	if c == ord('H'):
	  return HOME
	elif c == ord('F'):
	  return END
    
    return 0


class DialogListBox(DialogBox):
  # buttons is tuple/list of button text
  # list is list of items, will be truncated
  # default is default button, 0-indexed
  def show(self, buttons, list, default=0, start_index=0):
    # must have at least one button
    if not buttons:
      return -1
    # allow for buttons!
    start_indx = start_index
    cursor_indx = start_index
    entries = []
    for i in range(len(list)):
      entries.append([str(list[i]), curses.A_NORMAL])

    entries[cursor_indx][1] = curses.A_STANDOUT

    current_b = default
    # run our busy wait loop to get keyboard input
    while (1):
      ## Assume will diplay h - 1 entries
      item_disp_ct = self.h - 1

      # draw the dialog
      self.win.erase()	# note that this doesn't actually empty evert cell!
      self.win.box()
      self.win.addstr(0,2, self.title[0:self.w-2], curses.A_STANDOUT)

      # draw the message
      # we use "..." at top and bottom to replace scroll bars
      # fill with first self.h items
      j = 1
      ## Put a '...' if there are items "off the top"
      if start_indx > 0:
	self.win.addstr(1,1, "...", curses.A_NORMAL)
	item_disp_ct = item_disp_ct - 1
	j = j+1

      ## Figure out index of last item in display
      last_item_indx = start_indx + item_disp_ct

      ## Adjust last_item_indx for a short list
      if last_item_indx > len(entries):
        last_item_indx = len(entries)
        item_disp_ct = last_item_indx - start_indx
      else:
	last_item_indx = last_item_indx - 1
	item_disp_ct = item_disp_ct - 1
	self.win.addstr(self.h-1, 1, "...", curses.A_NORMAL)

      for i in range(start_indx, last_item_indx):
	self.win.addstr(j, 1, entries[i][0], entries[i][1])
	j = j + 1

      # draw the buttons
      #  compute # of buttons, then cut up lowest line, and center
      #  add <> around buttons
      #  selected button is standout, other normal
      y = self.h
      nb = len(buttons)
      dx = self.w/nb
      x0 = dx/2
      # paint the buttons - assume they won't overlap
      for i in range(len(buttons)):
	if current_b == i:
	  highlight = curses.A_STANDOUT
	else:
	  highlight = curses.A_NORMAL
	offset = (len(buttons[i])+2) / 2
	self.win.addstr(y, ((i+1)*dx)-x0-offset, "<"+buttons[i]+">", highlight)

      self.win.refresh()

      # wait for keystroke
      c = self.get_cmd()
      # change selected button
      if c == LEFT:
	current_b = current_b-1
	if current_b < 0:
	  current_b = len(buttons)-1
      elif c == RIGHT:
        current_b = current_b+1
        if current_b > len(buttons)-1:
	  current_b = 0
      elif c == SELECT:
	self.win.erase()
	return (current_b, cursor_indx)
      elif c == ESCAPE:
	self.win.erase()
	return (-1, -1)

      ### Remainder of entries manipulate the
      ### display in some way, so first, we
      ### clear the currently highlighted entry.

      entries[cursor_indx][1] = curses.A_NORMAL

      if c == UP:
	cursor_indx = cursor_indx - 1
      elif c == DOWN:
	cursor_indx = cursor_indx + 1
      elif c == PGDN:
	# Down one "page"
	cursor_indx = cursor_indx + item_disp_ct
      elif c == PGUP:
	# up one page
	cursor_indx = cursor_indx - item_disp_ct
      elif c == HOME:
	# go to beginning
	cursor_indx = 0
      elif c == END:
	cursor_indx = len(entries) - 1

      elif c >= 1000:
        jumpTo = string.lower(chr(32 + (c - 1000)))
        Match = 0
        ## Search forward from our index for a match
        for i in range(cursor_indx+1, len(entries)):
          if (string.lower(entries[i][0][0]) == jumpTo):
            cursor_indx = i
            Match = 1
            break
        ## No?  try from the beginning of the list
        if not Match:
          for i in range(cursor_indx):
            if (string.lower(entries[i][0][0]) == jumpTo):
              cursor_indx = i
              break

      ### Since the command might push stuff off
      ### one edge or the other, fix things up
      ### and redisplay.
      if cursor_indx < 0:
        cursor_indx = 0
      if cursor_indx >= len(entries):
        cursor_indx = len(entries) - 1
      if cursor_indx < start_indx: 
        start_indx = cursor_indx - (item_disp_ct / 2)
        self.win.clear()	# erase the window
	self.win.refresh()
        if start_indx < 0:
          start_indx = 0
      if cursor_indx >= start_indx + item_disp_ct:
        start_indx = cursor_indx - (item_disp_ct / 2)
        self.win.clear()	# erase the window
	self.win.refresh()
        if start_indx < 0:
          start_indx = 0

      entries[cursor_indx][1] = curses.A_STANDOUT


class DialogEntryBox(DialogBox):
  # buttons is tuple/list of button text
  # text is string to display - will be wrapped
  # entry area is on last line of text.
  # default is default button, 0-indexed
  def show(self, buttons, text, default=0):
    # must have at least one button
    if not buttons:
      return -1
    w = self.w+1
    # wrap the text at words
    lines = 0
    j = 0; k = -1; msg=[]
    for i in range(len(text)):
      j = j+1
      if text[i] == ' ':
	k = i
      if text[i] == '\n':
	j = 0; k = -1
	lines = lines+1
      if j > w:
	lines = lines+1
	j = 0
	# replace last space with \n
	if k == -1:
	  msg.insert(self.w-1,'\n')
	else:
	  if k == i:
	    msg.append(text[i])
	  msg.insert(k, '\n')
      else:
	msg.append(text[i])
    if lines > self.h:
      lines = self.h

    # draw the dialog
    self.win.erase()
    self.win.box()
    self.win.addstr(0,2, self.title[0:self.w-2], curses.A_STANDOUT)

    # draw the message
    #   parse msg for \n, incrementing lines
    y = 1; x = 1;
    for i in range(len(msg)):
      if msg[i] == '\n':
	y = y+1; x = 1
	if y > self.h:
	  break
      self.win.addch(y,x,ord(msg[i]), curses.A_NORMAL)
      x = x+1
    # store the position to start entering text
    ey = y; ex = x
    if ex >= w:
      ex = 1; ey = ey+1
    if ey > self.h:
      ey = self.h

    # draw the buttons
    #  compute # of buttons, then cut up lowest line, and center
    #  add <> around buttons
    #  selected button is standout, other normal
    current_b = default
    y = self.h
    nb = len(buttons)
    dx = w/nb
    x0 = dx/2
    # draw the buttons - assume they won't overlap
    for i in range(len(buttons)):
      if current_b == i:
	highlight = curses.A_STANDOUT
      else:
	highlight = curses.A_NORMAL
      offset = (len(buttons[i])+2) / 2
      self.win.addstr(y, ((i+1)*dx)-x0-offset, "<"+buttons[i]+">", highlight)

    self.win.box()
    self.win.addstr(0,2, self.title[0:self.w-2], curses.A_STANDOUT)

    entry_val = ""
    # run our busy wait loop to get keyboard input
    while (1):
      c = self.get_cmd()
      if c == LEFT:
	current_b = current_b-1
	if current_b < 0:
	  current_b = len(buttons)-1
      elif c == RIGHT:
        current_b = current_b+1
        if current_b > len(buttons)-1:
	  current_b = 0
      elif c == SELECT:
	return (current_b, entry_val)
      elif c == ESCAPE:
	return (-1, -1)
      elif c == BACKSPACE:
	if entry_val:
	  entry_val = entry_val[0:len(entry_val)-1]
      elif c >= 1000:
        char = chr(32 + (c - 1000))
        entry_val = entry_val+char

      # redraw the changeable bits of the dialog
      self.win.move(y,1)
      self.win.clrtoeol()
      self.win.move(ey,ex)
      self.win.clrtoeol()
      self.win.box()
      self.win.addstr(0,2, self.title[0:self.w-2], curses.A_STANDOUT)
      for i in range(len(buttons)):
	if current_b == i:
	  highlight = curses.A_STANDOUT
	else:
	  highlight = curses.A_NORMAL
	offset = (len(buttons[i])+2) / 2
	self.win.addstr(y, ((i+1)*dx)-x0-offset, "<"+buttons[i]+">", highlight)
      #   echo the current entry_val
      if len(entry_val) + ex > w:
	display_val = entry_val[len(entry_val)-(w-ex):len(entry_val)]
      else:
	display_val = entry_val
      for i in range(len(display_val)):
	self.win.addch(ey, ex+i, ord(display_val[i]), curses.A_NORMAL)
