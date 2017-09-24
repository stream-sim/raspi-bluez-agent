#!/usr/bin/python

from __future__ import absolute_import, print_function, unicode_literals

from gi.repository import GObject

import sys
import dbus
import dbus.service
import dbus.mainloop.glib

SERVICE_NAME = "org.bluez"
ADAPTER_INTERFACE = SERVICE_NAME + ".Adapter1"
DEVICE_INTERFACE = SERVICE_NAME + ".Device1"
AGENT_INTERFACE = 'org.bluez.Agent1'
AGENT_PATH = "/bluez/agent"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

bus = None
object_manager = None
agent_manager = None
adapter = None
adapter_props = None
devices = {}
connected_device = None

def get_managed_objects():
	agent_manager = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
	return agent_manager.GetManagedObjects()

def find_adapter(pattern=None):
	return find_adapter_in_objects(get_managed_objects(), pattern)

def find_adapter_in_objects(objects, pattern=None):
	for path, ifaces in objects.iteritems():
		adapter = ifaces.get(ADAPTER_INTERFACE)
		if adapter is None:
			continue
		if not pattern or pattern == adapter["Address"] or \
							path.endswith(pattern):
			obj = bus.get_object(SERVICE_NAME, path)
			return dbus.Interface(obj, ADAPTER_INTERFACE), dbus.Interface(obj, PROPERTIES_INTERFACE)
	raise Exception("Bluetooth adapter not found")

def find_device(device_address, adapter_pattern=None):
	return find_device_in_objects(get_managed_objects(), device_address,
								adapter_pattern)

def find_device_in_objects(objects, device_address, adapter_pattern=None):
	path_prefix = ""
	if adapter_pattern:
		adapter = find_adapter_in_objects(objects, adapter_pattern)
		path_prefix = adapter.object_path
	for path, ifaces in objects.iteritems():
		device = ifaces.get(DEVICE_INTERFACE)
		if device is None:
			continue
		if (device["Address"] == device_address and
						path.startswith(path_prefix)):
			obj = bus.get_object(SERVICE_NAME, path)
			return dbus.Interface(obj, DEVICE_INTERFACE)

	raise Exception("Bluetooth device not found")

def set_trusted(path):
	props = dbus.Interface(bus.get_object(SERVICE_NAME, path), PROPERTIES_INTERFACE)
	props.Set("org.bluez.Device1", "Trusted", True)

def dev_connect(path):
	dev = dbus.Interface(bus.get_object(SERVICE_NAME, path), DEVICE_INTERFACE)
	dev.Connect()

def dev_disconnect(path):
	dev = dbus.Interface(bus.get_object(SERVICE_NAME, path), DEVICE_INTERFACE)
	dev.Disconnect()

class Rejected(dbus.DBusException):
	_dbus_error_name = "org.bluez.Error.Rejected"

class Agent(dbus.service.Object):
	exit_on_release = True

	def set_exit_on_release(self, exit_on_release):
		self.exit_on_release = exit_on_release

	@dbus.service.method(AGENT_INTERFACE,
					in_signature="", out_signature="")
	def Release(self):
		print("Release")
		if self.exit_on_release:
			mainloop.quit()

	@dbus.service.method(AGENT_INTERFACE,
					in_signature="os", out_signature="")
	def AuthorizeService(self, device, uuid):
		print("AuthorizeService (%s, %s)" % (device, uuid))

	@dbus.service.method(AGENT_INTERFACE,
					in_signature="o", out_signature="s")
	def RequestPinCode(self, device):
		print("RequestPinCode (%s)" % (device))
		set_trusted(device)
		# NoInputNoDisplay capability should prevent this
		return dbus.UInt32(1111)

	@dbus.service.method(AGENT_INTERFACE,
					in_signature="o", out_signature="u")
	def RequestPasskey(self, device):
		print("RequestPasskey (%s)" % (device))
		set_trusted(device)
		# NoInputNoDisplay capability should prevent this
		return dbus.UInt32(1111)

	@dbus.service.method(AGENT_INTERFACE,
					in_signature="ouq", out_signature="")
	def DisplayPasskey(self, device, passkey, entered):
		print("DisplayPasskey (%s, %06u entered %u)" %
						(device, passkey, entered))

	@dbus.service.method(AGENT_INTERFACE,
					in_signature="os", out_signature="")
	def DisplayPinCode(self, device, pincode):
		print("DisplayPinCode (%s, %s)" % (device, pincode))

	@dbus.service.method(AGENT_INTERFACE,
					in_signature="ou", out_signature="")
	def RequestConfirmation(self, device, passkey):
		print("RequestConfirmation (%s, %06d)" % (device, passkey))
		set_trusted(device)

	@dbus.service.method(AGENT_INTERFACE,
					in_signature="o", out_signature="")
	def RequestAuthorization(self, device):
		print("RequestAuthorization (%s)" % (device))

	@dbus.service.method(AGENT_INTERFACE,
					in_signature="", out_signature="")
	def Cancel(self):
		print("Cancel")

class Device:

	def __init__(self, path, props):
		self.path = path
		obj = bus.get_object(SERVICE_NAME, path)
		obj.connect_to_signal("PropertiesChanged", self.OnPropertiesChanged, dbus_interface=PROPERTIES_INTERFACE)
		self.OnPropertiesChanged(DEVICE_INTERFACE, props, None)

	def UpdateConnectedState(self, connected):
		global connected_device
		if connected == True and connected_device != self.path:
			if connected_device != None:
				print("Disconnect device: " + repr(connected_device))
				dev_disconnect(connected_device)
			connected_device = self.path
			print("New connected device: " + repr(self.path))

		if connected == False and connected_device == self.path:
			print("Device disconnected: " + repr(connected_device))
			connected_device = None

	def OnPropertiesChanged(self, interface, props, inval_props):
		print("Device properties changed: " + repr(interface))
		# print("props: " + repr(props))
		# print("inval_props: " + repr(inval_props))
		if 'Connected' in props:
			connected = props['Connected']
			self.UpdateConnectedState(connected)


def on_interfaces_added(path, ifaces):
	if ADAPTER_INTERFACE in ifaces:
		do_initialize_adapter()

	device_props = ifaces.get(DEVICE_INTERFACE)
	if device_props is None:
		return;

	# TODO: check if device has audio source capability
	print("Device added: " + repr(path))

	global devices
	device = Device(path, device_props)
	devices[path] = device

	device.OnPropertiesChanged(DEVICE_INTERFACE, device_props, None)

def on_interfaces_removed(path, ifaces):
	if ADAPTER_INTERFACE in ifaces:
		global adapter, adapter_props
		adapter = None
		adapter_props = None

	if DEVICE_INTERFACE in ifaces:
		print("Device removed: " + repr(path))
		del devices[path]

def do_initialize_adapter():
	global adapter, adapter_props
	adapter, adapter_props = find_adapter()

	if adapter_props is None:
		return;

	adapter_props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(True, variant_level=1))
	adapter_props.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(0, variant_level=1))

def on_name_owner_changed(name):
	print("Owner changed " + repr(name))
	global object_manager, agent_manager, adapter, adapter_props, devices, connected_device
	if name:
		object_manager = bus.get_object(SERVICE_NAME, "/")
		object_manager.connect_to_signal("InterfacesAdded", on_interfaces_added, dbus_interface="org.freedesktop.DBus.ObjectManager")
		object_manager.connect_to_signal("InterfacesRemoved", on_interfaces_removed, dbus_interface="org.freedesktop.DBus.ObjectManager")

		agent_manager = dbus.Interface(bus.get_object(SERVICE_NAME, "/org/bluez"), "org.bluez.AgentManager1")
		agent_manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
		agent_manager.RequestDefaultAgent(AGENT_PATH)
		print("Agent registered")

		agent_manager_interface = dbus.Interface(object_manager, "org.freedesktop.DBus.ObjectManager")
		objects = agent_manager_interface.GetManagedObjects()
		for path, ifaces in objects.iteritems():
			on_interfaces_added(path, ifaces)

		do_initialize_adapter()

	else:
		object_manager = None
		agent_manager = None
		adapter = None
		adapter_props = None
		devices = {}
		connected_device = None

def do_main_program():
	dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

	global bus
	bus = dbus.SystemBus()
	bus.watch_name_owner(SERVICE_NAME, on_name_owner_changed)

	agent = Agent(bus, AGENT_PATH)

	mainloop = GObject.MainLoop()
	mainloop.run()

def do_program_cleanup():
	adapter_props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(False, variant_level=1))
	adapter_props.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(180, variant_level=1))

	agent_manager.UnregisterAgent(AGENT_PATH)
	print("Agent unregistered")

if __name__ == '__main__':

	try:
		do_main_program()

	except KeyboardInterrupt:
		do_program_cleanup()
		sys.exit(0)
