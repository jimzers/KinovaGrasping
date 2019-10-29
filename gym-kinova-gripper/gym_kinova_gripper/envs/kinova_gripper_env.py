#!/usr/bin/env python3

###############
# Author: Yi Herng Ong
# Purpose: Kinova 3-fingered gripper in mujoco environment
# Summer 2019

###############


import gym
from gym import utils, spaces
from gym.utils import seeding
# from gym.envs.mujoco import mujoco_env
import numpy as np
from mujoco_py import MjViewer, load_model_from_path, MjSim
# from PID_Kinova_MJ import *
import math
import matplotlib.pyplot as plt
import time
import os, sys
from scipy.spatial.transform import Rotation as R
import random
import pickle
import pdb
import torch
import torch.nn as nn
import torch.nn.functional as F

# resolve cv2 issue 
# sys.path.remove('/opt/ros/kinetic/lib/python2.7/dist-packages')
# frame skip = 20
# action update time = 0.002 * 20 = 0.04
# total run time = 40 (n_steps) * 0.04 (action update time) = 1.6

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


class KinovaGripper_Env(gym.Env):
	metadata = {'render.modes': ['human']}
	def __init__(self, arm_or_end_effector="hand", frame_skip=4):
		file_dir = os.path.dirname(os.path.realpath(__file__))
		if arm_or_end_effector == "arm":
			self._model = load_model_from_path(file_dir + "/kinova_description/j2s7s300.xml")
			full_path = file_dir + "/kinova_description/j2s7s300.xml"
		elif arm_or_end_effector == "hand":
			self._model = load_model_from_path(file_dir + "/kinova_description/j2s7s300_end_effector.xml")
			full_path = file_dir + "/kinova_description/j2s7s300_end_effector.xml"
		else:
			print("CHOOSE EITHER HAND OR ARM")
			raise ValueError
		
		self._sim = MjSim(self._model)
		
		self._viewer = None
		# self.viewer = None


		self._timestep = self._sim.model.opt.timestep
		self._torque = [0,0,0,0]
		self._velocity = [0,0,0,0]

		self._jointAngle = [0,0,0,0]
		self._positions = [] # ??
		self._numSteps = 0
		self._simulator = "Mujoco"
		self.action_scale = 0.0333
		self.max_episode_steps = 50
		# Parameters for cost function
		self.state_des = 0.20 
		self.initial_state = np.array([0.0, 0.0, 0.0, 0.0])
		self.frame_skip = frame_skip
		self.all_states = None
		self.action_space = spaces.Box(low=np.array([-0.8, -0.8, -0.8, -0.8]), high=np.array([0.8, 0.8, 0.8, 0.8]), dtype=np.float32)
		self.state_rep = "metric" # change accordingly
		# self.action_space = spaces.Box(low=np.array([-0.2]), high=np.array([0.2]), dtype=np.float32)
		# self.action_space = spaces.Box(low=np.array([-0.8, -0.8, -0.8]), high=np.array([0.8, 0.8, 0.8]), dtype=np.float32)

		min_hand_xyz = [-0.1, -0.1, 0.0, -0.1, -0.1, 0.0, -0.1, -0.1, 0.0,-0.1, -0.1, 0.0, -0.1, -0.1, 0.0,-0.1, -0.1, 0.0, -0.1, -0.1, 0.0]
		min_obj_xyz = [-0.1, -0.01, 0.0]
		min_joint_states = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
		min_obj_size = [0.0, 0.0, 0.0]
		min_finger_obj_dist = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
		min_dot_prod = [0.0]

		max_hand_xyz = [0.1, 0.1, 0.5, 0.1, 0.1, 0.5, 0.1, 0.1, 0.5,0.1, 0.1, 0.5, 0.1, 0.1, 0.5,0.1, 0.1, 0.5, 0.1, 0.1, 0.5]
		max_obj_xyz = [0.1, 0.7, 0.5]
		max_joint_states = [0.2, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0]
		max_obj_size = [0.5, 0.5, 0.5]
		max_finger_obj_dist = [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]	
		max_dot_prod = [1.0]

		# print()
		if self.state_rep == "global" or self.state_rep == "local":

			obs_min = min_hand_xyz + min_obj_xyz + min_joint_states + min_obj_size + min_finger_obj_dist + min_dot_prod
			obs_min = np.array(obs_min)
			# print(len(obs_min))

			obs_max = max_hand_xyz + max_obj_xyz + max_joint_states + max_obj_size + max_finger_obj_dist + max_dot_prod
			obs_max = np.array(obs_max)
			# print(len(obs_max))

			self.observation_space = spaces.Box(low=obs_min , high=obs_max, dtype=np.float32)
		elif self.state_rep == "metric":
			obs_min = list(np.zeros(17)) + [-0.1, -0.1, 0.0] + min_obj_xyz + min_joint_states + min_obj_size + min_finger_obj_dist + min_dot_prod
			obs_max = list(np.full(17, np.inf)) + [0.1, 0.1, 0.5] + max_obj_xyz + max_joint_states + max_obj_size + max_finger_obj_dist + max_dot_prod
			self.observation_space = spaces.Box(low=np.array(obs_min) , high=np.array(obs_max), dtype=np.float32)

		elif self.state_rep == "joint_states":
			obs_min = min_joint_states + min_obj_xyz + min_obj_size + min_dot_prod
			obs_max = max_joint_states + max_obj_xyz + max_obj_size + max_dot_prod
			self.observation_space = spaces.Box(low=np.array(obs_min) , high=np.array(obs_max), dtype=np.float32)

		self.Grasp_net = GraspValid_net(48).to(device) 
		trained_model = "/home/graspinglab/NCS_data/data_cube_9_grasp_classifier_10_17_19_1734.pt"
		model = torch.load(trained_model)
		self.Grasp_net.load_state_dict(model)
		self.Grasp_net.eval()


	# get 3D transformation matrix of each joint
	def _get_trans_mat(self, joint_geom_name):
		finger_joints = joint_geom_name	
		finger_pose = []
		empty = np.array([0,0,0,1])
		for each_joint in finger_joints:
			arr = []
			for axis in range(3):
				temp = np.append(self._sim.data.get_geom_xmat(each_joint)[axis], self._sim.data.get_geom_xpos(each_joint)[axis])
				arr.append(temp)
			arr.append(empty)
			arr = np.array(arr)
			finger_pose.append(arr)	
		return np.array(finger_pose)


	def _get_local_pose(self, mat):
		rot_mat = []
		trans = []
		# print(mat)
		for i in range(3):
			orient_temp = []

			for j in range(4):
				if j != 3:
					orient_temp.append(mat[i][j])
				elif j == 3:
					trans.append(mat[i][j])
			rot_mat.append(orient_temp)
		pose = list(trans) # + list(euler_vec)

		return pose

	def _get_joint_states(self):
		arr = []
		for i in range(7):
			arr.append(self._sim.data.sensordata[i])

		return arr # it is a list

	# return global or local transformation matrix
	def _get_obs(self, state_rep=None):
		if state_rep == None:
			state_rep = self.state_rep

		# states rep
		obj_pose = self._get_obj_pose()
		dot_prod = self._get_dot_product()
		wrist_pose  = self._sim.data.get_geom_xpos("palm")
		joint_states = self._get_joint_states()
		obj_size = self._sim.model.geom_size[-1] 
		finger_obj_dist = self._get_finger_obj_dist()

		palm = self._get_trans_mat(["palm"])[0]
		finger_joints = ["f1_prox", "f2_prox", "f3_prox", "f1_dist", "f2_dist", "f3_dist"]
		finger_joints_transmat = self._get_trans_mat(["f1_prox", "f2_prox", "f3_prox", "f1_dist", "f2_dist", "f3_dist"])
		fingers_6D_pose = []
		if state_rep == "global":			
			for joint in finger_joints:
				# rot_mat = R.from_dcm(self._sim.data.get_geom_xmat(joint))
				# euler_vec = rot_mat.as_euler('zyx', degrees=True)
				trans = self._sim.data.get_geom_xpos(joint)
				trans = list(trans)
				# trans += list(euler_vec)
				for i in range(3):
					fingers_6D_pose.append(trans[i])
			fingers_6D_pose = fingers_6D_pose + list(wrist_pose) + list(obj_pose) + joint_states + [obj_size[0], obj_size[1], obj_size[2]*2] + finger_obj_dist + [dot_prod]

		elif state_rep == "local":
			finger_joints_local = []
			palm_inverse = np.linalg.inv(palm)
			for joint in range(len(finger_joints_transmat)):
				joint_in_local_frame = np.matmul(finger_joints_transmat[joint], palm_inverse)
				pose = self._get_local_pose(joint_in_local_frame)
				for i in range(3):
					fingers_6D_pose.append(pose[i])
			fingers_6D_pose = fingers_6D_pose + list(wrist_pose) + list(obj_pose) + joint_states + [obj_size[0], obj_size[1], obj_size[2]*2] + finger_obj_dist + [dot_prod]

		elif state_rep == "metric":
			fingers_6D_pose = self._get_rangefinder_data()
			fingers_6D_pose = fingers_6D_pose + list(wrist_pose) + list(obj_pose) + joint_states + [obj_size[0], obj_size[1], obj_size[2]*2] + finger_obj_dist + [dot_prod]

		elif state_rep == "joint_states":
			fingers_6D_pose = joint_states + list(obj_pose) + [obj_size[0], obj_size[1], obj_size[2]*2] + [dot_prod] 

		return fingers_6D_pose 

	def _get_finger_obj_dist(self):
		# finger_joints = ["palm", "f1_prox", "f2_prox", "f3_prox", "f1_dist", "f2_dist", "f3_dist"]
		finger_joints = ["palm_1", "f1_prox","f1_prox_1", "f2_prox", "f2_prox_1", "f3_prox", "f3_prox_1", "f1_dist", "f1_dist_1", "f2_dist", "f2_dist_1", "f3_dist", "f3_dist_1"]

		obj = self._get_obj_pose()
		dists = []
		for i in finger_joints:
			pos = self._sim.data.get_site_xpos(i)
			dist = np.absolute(pos[0:2] - obj[0:2])
			dist[0] -= 0.0175
			temp = np.linalg.norm(dist)
			dists.append(temp)
			# pdb.set_trace()
		return dists


	# get range data from 1 step of time 
	# Uncertainty: rangefinder could only detect distance to the nearest geom, therefore it could detect geom that is not object
	def _get_rangefinder_data(self):
		range_data = []
		for i in range(17):
			range_data.append(self._sim.data.sensordata[i+7])

		return range_data

	def _get_obj_pose(self):
		arr = self._sim.data.get_geom_xpos("cube")
		return arr


	# Function to return dot product based on object location
	def _get_dot_product(self):
		obj_state = self._get_obj_pose()
		hand_pose = self._sim.data.get_body_xpos("j2s7s300_link_7")
		obj_state_x = abs(obj_state[0] - hand_pose[0])
		obj_state_y = abs(obj_state[1] - hand_pose[1])
		obj_vec = np.array([obj_state_x, obj_state_y])
		obj_vec_norm = np.linalg.norm(obj_vec)
		obj_unit_vec = obj_vec / obj_vec_norm

		center_x = abs(0.0 - hand_pose[0])
		center_y = abs(0.0 - hand_pose[1])
		center_vec = np.array([center_x, center_y])
		center_vec_norm = np.linalg.norm(center_vec)
		center_unit_vec = center_vec / center_vec_norm
		dot_prod = np.dot(obj_unit_vec, center_unit_vec)

		return dot_prod**20 # cuspy to get distinct reward

	'''
	Testing reward function (not useful, saved it for testing purposes)
	'''
	def _get_dist_reward(self, state, action):
		
		f1 = self._sim.data.get_geom_xpos("f1_dist")

		target_pos = np.array([0.05813983, 0.01458329])
		target_vel = np.array([0.0])

		f1_pos_err = (target_pos[0] - state[0])**2 + (target_pos[1] - state[1])**2
		# f1_curr = math.sqrt((0.05813983 - state[0])**2 + (0.01458329 - state[1])**2)
		# f1_curr = (0.058 - state[0])**2 + (0.014 - state[1])**2
		f1_vel_err = (target_vel[0] - action)**2

		f1_pos_reward = 12*(math.exp(-100*f1_pos_err) - 1)
		f1_vel_reward = 4.5*(math.exp(-f1_vel_err) - 1)

		reward = f1_pos_reward 

		return reward

	# def _expertvelocity_reward(self, action):
		# input 

	'''
	Reward function (Actual)
	'''
	def _get_reward(self):

		# object height target
		obj_target = 0.2

		grasp_reward = 0.0
		obs = self._get_obs(state_rep="global") 
		inputs = torch.FloatTensor(np.array(obs)).to(device)
		if np.max(np.array(obs[41:47])) < 0.035 or np.max(np.array(obs[35:41])) < 0.015: 
			outputs = self.Grasp_net(inputs).cpu().data.numpy().flatten()
			if outputs == 1.0:
				grasp_reward = 5.0
			else:
				grasp_reward = 0.0
		
		if abs(obs[23] - obj_target) < 0.005 or (obs[23] >= obj_target):
			lift_reward = 50.0
			done = True
		else:
			lift_reward = 0.0
			done = False

		finger_reward = -np.sum((np.array(obs[41:47])) + (np.array(obs[35:41])))

		# if finger_reward < self.prev_fr:
		# 	# pdb.set_trace()
		# 	finger_reward = self.prev_fr

		# print(np.array(obs[41:47]), np.array(obs[35:41]))
		reward = 0.2*finger_reward + lift_reward + grasp_reward
		# print(0.2*finger_reward, reward)
		# self.prev_fr = finger_reward
		# print(0.2*finger_reward, grasp_reward, lift_reward)
		return reward, {}, done

	# only set proximal joints, cuz this is an underactuated hand
	def _set_state(self, states):
		self._sim.data.qpos[0] = states[0]
		self._sim.data.qpos[1] = states[1]
		self._sim.data.qpos[3] = states[2]
		self._sim.data.qpos[5] = states[3]
		self._sim.data.set_joint_qpos("cube", [states[4], states[5], states[6], 1.0, 0.0, 0.0, 0.0])
		self._sim.forward()

	def _get_obj_size(self):
		return self._sim.model.geom_size[-1]


	def _set_obj_size(self, random_type=False, chosen_type="box", random_size=False):
		self.hand_param = {}
		self.hand_param["span"] = 0.175
		self.hand_param["depth"] = 0.08
		self.hand_param["height"] = 0.15 # including distance between table and hand

		geom_type = {"box": [6, 3], "cylinder": [5, 3], "sphere": [2, 1]}
		geom_index = 0
		if random_type:
			chosen_type = random.choice(list(geom_type.keys()))
			geom_index = geom_type[chosen_type][0]
			geom_dim_size = geom_type[chosen_type][1]

		else:
			geom_index = geom_type[chosen_type][0]
			geom_dim_size = geom_type[chosen_type][1]

		# Cube w: 0.1, 0.2, 0.3
		# Cylinder w: 0.1, 0.2, 0.3
		# Sphere w: 0.1, 0.2, 0.3

		# Cube & Cylinder
		width_max = self.hand_param["span"] * 0.30 
		width_mid = self.hand_param["span"] * 0.20
		width_min = self.hand_param["span"] * 0.10 
		width_choice = np.array([width_min, width_mid, width_max])

		height_max = self.hand_param["height"] * 0.80
		height_mid = self.hand_param["height"] * 0.6667
		height_min = self.hand_param["height"] * 0.50
		height_choice = np.array([height_min, height_mid, height_max])

		# Sphere
		radius_max = self.hand_param["span"] * 0.30
		radius_mid = self.hand_param["span"] * 0.28 
		radius_min = self.hand_param["span"] * 0.25
		radius_choice = np.array([radius_min, radius_mid, radius_max])

		geom_dim = np.array([])

		if random_size:
			# if box and cylinder
			if geom_index == 6 or geom_index == 5:
				# geom_dim = []
				width = random.choice(width_choice)
				height = random.choice(height_choice)
				geom_dim = np.array([width, width, height])
			# if sphere
			elif geom_index == 2:
				width = random.choice(radius_choice)
			else:
				raise ValueError
				geom_dim = np.array([width])
				
			return geom_index, geom_dim
		else:
			# return medium size box
			width = (self.hand_param["span"] * 0.20) / 2
			height = 0.05

			geom_dim = np.array([width, width, height])

			return geom_index, geom_dim

	def randomize_initial_pose(self):
		x = [0.05, 0.04, 0.03, 0.02, -0.05, -0.04, -0.03, -0.02]
		y = [0.0, 0.02, 0.03, 0.04]
		rand_x = random.choice(x)
		if rand_x == 0.05 or rand_x == -0.05:
			rand_y = 0.0
		elif rand_x == 0.04 or rand_x == -0.04:
			rand_y = random.uniform(0.0, 0.02)
		elif rand_x == 0.03 or rand_x == -0.03:
			rand_y = random.uniform(0.0, 0.03)
		elif rand_x == 0.02 or rand_x == -0.02:
			rand_y = random.uniform(0.0, 0.04)
		return rand_x, rand_y


	def reset(self):
		# geom_index, geom_dim = self._set_obj_size(random_type=True, random_size=True)
		# self._sim.model.geom_type[-1] = geom_index
		# self._sim.model.geom_size[-1] = geom_dim
		# print(geom_dim)
		# if len(geom_dim) > 1:
		# 	obj_height = geom_dim[2]
		# else:
		# 	obj_height = geom_dim[0]

		x, y = self.randomize_initial_pose()
		# # print(x, y)

		self.all_states_1 = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.05])
		self.all_states_2 = np.array([0.0, 0.9, 0.9, 0.9, 0.0, -0.01, 0.05])
		self.all_states = [self.all_states_1 , self.all_states_2] 
		random_start = np.random.randint(2)

		self.obj_original_state = np.array([0.05, 0.0])
		self._set_state(self.all_states[0])		
		self.init_dotprod = self._get_dot_product()
		self.init_pose = np.array([x, y, 0.05])

		states = self._get_obs()

		self.prev_fr = 0.0
		self.prev_r = 0.0
		return states

	def render(self, mode='human'):
		if self._viewer is None:
			self._viewer = MjViewer(self._sim)
		self._viewer.render()

	def close(self):
		if self._viewer is not None:
			self._viewer = None

	def seed(self, seed=None):
		self.np_random, seed = seeding.np_random(seed)
		return [seed]

	def step(self, action, render=False):
		total_reward = 0

		for _ in range(self.frame_skip):
			# self._sim.data.ctrl[0] = 0.0
			# f_action = np.array([0.4, 0.2, 0.2])
			if action[0] < 0.0:
				self._sim.data.ctrl[0] = 0.0
			else:	
				self._sim.data.ctrl[0] = (action[0] / 0.8) * 0.2
			
			for i in range(3):
				# vel = action[i]
				if action[i] < 0.0:
					self._sim.data.ctrl[i+1] = 0.0
				else:	
					self._sim.data.ctrl[i+1] = action[i+1]

			self._sim.step()

		obs = self._get_obs()
		total_reward, info, done = self._get_reward()

		return obs, total_reward, done, info

class GraspValid_net(nn.Module):
	def __init__(self, state_dim):
		super(GraspValid_net, self).__init__()
		self.l1 = nn.Linear(state_dim, 256)
		self.l2 = nn.Linear(256, 256)
		self.l3 = nn.Linear(256, 1)

	def forward(self, state):
		# pdb.set_trace()

		a = F.relu(self.l1(state))
		a = F.relu(self.l2(a))
		a =	torch.sigmoid(self.l3(a))
		return a
