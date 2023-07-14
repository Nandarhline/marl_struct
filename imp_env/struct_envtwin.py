import numpy as np
import scipy.stats as stats
import random
import os
from imp_env.imp_env import ImpEnv

class Struct_twin(ImpEnv):
    """ k-out-of-n system and digital twin (Struct_twin) class. 

    Attributes:
        n_comp: Integer indicating the number of components.
        discount_reward: Float indicating the discount factor.
        k_comp: Integer indicating the number 'k' (out of n) components in the system.
        campaign_cost: Boolean indicating whether a global campaign cost is considered in the reward model.
        ep_length: Integer indicating the number of time steps in the finite horizon.
        proba_size: Integer indicating the number of bins considered in the discretisation of the damage probability.
        n_obs_inspection: Integer indicating the number of potential outcomes resulting from an inspection.
        actions_per_agent: Integer indicating the number of actions that an agent can take.
        initial_damage_proba: Numpy array containing the initial damage probability.
        transition_model: Numpy array containing the transition model that drives the environment dynamics.
        inspection_model: Numpy array containing the inspection model.
        initial_alpha: Numpy array contaning the containing the initial correlation factor.
        initial_damage_proba_correlated: Numpy array containing the initial damage probability given the correlation factor.
        damage_proba_after_repair_correlated: Numpy array containing the initial damage probability given the correlation factor after a repair is conducted.
        agent_list: Dictionary categorising the number of agents.
        time_step: Integer indicating the current time step.
        damage_proba: Numpy array contatining the current damage probability.
        d_rate: Numpy array contatining the current deterioration rate.
        observations: Dictionary listing the observations received by the agents in the Dec-POMDP.

    Methods: 
        reset
        step
        pf_sys
        immediate_cost
        belief_update_uncorrelated
        belief_update_correlated
    """

    def __init__(self, config=None):
        if config is None:
            config = {"n_comp": 3,
                      "discount_reward": 0.95,
                      "k_comp": 3,
                      "campaign_cost": False}
        assert "n_comp" in config and \
               "discount_reward" in config and \
               "k_comp" in config and \
               "campaign_cost" in config, \
            "Missing env config"

        self.n_comp = config["n_comp"]
        self.discount_reward = config["discount_reward"]
        self.k_comp = self.n_comp - 1 if config["k_comp"] is None \
            else config["k_comp"]
        self.campaign_cost = config["campaign_cost"]
        self.time = 0
        self.ep_length = 20  # Horizon length

        self.obs_per_agent_multi = None  
        self.obs_total_single = None  

        ### Loading the underlying POMDP model ###
        drmodel = np.load(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                'pomdp_models/Dr203020_Tw03.npz'))

        self.d_interv = drmodel['d_interv'] 
        self.q_interv = drmodel['q_interv'] 
        self.n_st_comp = len(self.d_interv)-1  # Crack states (fatigue hotspot damage states)
        self.n_st_stress = len(self.q_interv)-1  # Stress states (fatigue hotspot damage states)
        
        self.n_obs = 2*self.n_st_stress 
        # Total number of observations (crack detected * n_st_stress + crack not detected * n_st_stress)
        self.actions_per_agent = 6
        
        # To build oservation model of digital twin 
        self.q_ref = -1e100
        self.q_ref = np.append(self.q_ref, self.q_interv[1:])
        self.q_ref[-1] = 1e100
        self.q_ref = np.tile(self.q_ref,(100,1)).T
        
        # Initial probability distributions for crack-stress joint state, sensor state and digital twin uncertainty
        self.belief0 = np.tile(drmodel['belief0'],(self.n_comp,1))
        self.belief0_twin = np.tile(drmodel['belief0_twin'],(self.n_comp,1))          
        self.belief0_eps = np.tile(drmodel['belief0_eps'],(self.n_comp,1))
        
        # Transition model for sensor state
        self.T0 = drmodel['T0'] 
        self.Tr = drmodel['Tr']
        self.T0_twin = drmodel['T0_twin']
        self.Tr_twin = drmodel['Tr_twin']
        self.Ts_twin = drmodel['Ts_twin']
        
        # Observation models for inspection and physical sensor   
        self.O_ins = drmodel['O_ins']
        self.O_monitor = drmodel['O_monitor']
        self.O_ins_monitor = drmodel['O_ins_monitor']

        self.agent_list = ["agent_" + str(i) for i in range(self.n_comp)]

        self.time_step = 0
        self.beliefs = self.belief0
        self.beliefs_twin = self.belief0_twin
        self.beliefs_eps = self.belief0_eps
        self.d_rate = np.zeros((self.n_comp, 1), dtype=int)
        self.observations = None

        # Reset struct_env.
        self.reset()

    def reset(self):
        # We need the following line to seed self.np_random
        # super().reset(seed=seed)

        # Choose the agent's belief
        self.time_step = 0
        self.beliefs = self.belief0
        self.beliefs_twin = self.belief0_twin
        self.beliefs_eps = self.belief0_eps
        self.d_rate = np.zeros((self.n_comp, 1), dtype=int)
        self.observations = {}
        for i in range(self.n_comp):
            belief_dq = np.reshape(self.beliefs[i,:], [self.n_st_comp,self.n_st_stress])
            d_margin = np.sum(belief_dq, axis=1)
            q_margin = np.sum(belief_dq, axis=0)
            self.observations[self.agent_list[i]] = np.concatenate(
                (d_margin, q_margin, self.beliefs_eps[i], [self.time_step / self.ep_length]))

        return self.observations

    def step(self, action: dict):
        action_ = np.zeros(self.n_comp, dtype=int)
        for i in range(self.n_comp):
            action_[i] = action[self.agent_list[i]]


        observation_, belief_prime, belief_twin_prime, belief_eps_prime, drate_prime = \
            self.belief_update_uncorrelated(self.beliefs, self.beliefs_twin, self.beliefs_eps, 
                action_, self.d_rate)

        reward_ = self.immediate_cost(self.beliefs, action_, belief_prime, self.d_rate)
        reward = self.discount_reward ** self.time_step * reward_.item()  # Convert float64 to float

        rewards = {}
        for i in range(self.n_comp):
            rewards[self.agent_list[i]] = reward

        self.time_step += 1
        

        self.observations = {}
        for i in range(self.n_comp):
            belief_dq = np.reshape(belief_prime[i,:], [self.n_st_comp,self.n_st_stress])
            d_margin = np.sum(belief_dq, axis=1)
            q_margin = np.sum(belief_dq, axis=0)
            self.observations[self.agent_list[i]] = np.concatenate(
                (d_margin, q_margin, belief_eps_prime[i], [self.time_step / self.ep_length]))

        self.beliefs = belief_prime
        self.beliefs_twin = belief_twin_prime
        self.beliefs_eps = belief_eps_prime
        self.d_rate = drate_prime

        # An episode is done if the agent has reached the target
        done = self.time_step >= self.ep_length

        # info = {"belief": self.beliefs}
        return self.observations, rewards, done, observation_

    def pf_sys(self, pf, k):
        """compute pf_sys for k-out-of-n components"""
        n = pf.size
        # k = ncomp-1
        nk = n - k
        m = k + 1
        A = np.zeros(m + 1)
        A[1] = 1
        L = 1
        for j in range(1, n + 1):
            h = j + 1
            Rel = 1 - pf[j - 1]
            if nk < j:
                L = h - nk
            if k < j:
                A[m] = A[m] + A[k] * Rel
                h = k
            for i in range(h, L - 1, -1):
                A[i] = A[i] + (A[i - 1] - A[i]) * Rel
        PF_sys = 1 - A[m]
        return PF_sys

    def immediate_cost(self, B, a, B_, drate):
        """ immediate reward (-cost),
         based on current damage state and action """
        cost_system = 0
        PF = np.sum(B[:, -self.n_st_stress:], axis=1) # the last n_st_stress states (d is the outer loop)
        PF_ = np.sum(B_[:, -self.n_st_stress:], axis=1).copy()
        campaign_executed = False
        for i in range(self.n_comp):
            if a[i] == 4 or a[i] == 5: # Perfect repair
                cost_system += -10
                if a[i] == 5: # Install sensor
                    cost_system += -3
                if self.campaign_cost and not campaign_executed:
                    campaign_executed = True # Campaign executed
            else: # Do-nothing
                Bplus = B[i, :].dot(self.T0[drate[i, 0]]) 
                PF_[i] = np.sum(Bplus[-self.n_st_stress:])
                if a[i] == 1: # Inspection
                    cost_system += -1   
                    if self.campaign_cost and not campaign_executed:
                        campaign_executed = True # Campaign executed
                if a[i] == 2: # Install sensor
                    cost_system += -3 
                    if self.campaign_cost and not campaign_executed:
                        campaign_executed = True # Campaign executed
                if a[i] == 3: # Inspection and install sensor
                    cost_system += -4 
                    if self.campaign_cost and not campaign_executed:
                        campaign_executed = True # Campaign executed
        if self.n_comp < 2:  # single component setting
            PfSyS_ = PF_
            PfSyS = PF
        else:
            PfSyS_ = self.pf_sys(PF_, self.k_comp)
            PfSyS = self.pf_sys(PF, self.k_comp)
        if PfSyS_ < PfSyS:
            cost_system += PfSyS_ * (-500)
        else:
            cost_system += (PfSyS_ - PfSyS) * (-500)
        if campaign_executed: # Assign campaign cost
            cost_system += -5
        return cost_system

    def belief_update_uncorrelated(self, b, btwin, beps, a, drate):
        """Bayesian belief update based on
         previous belief, current observation, and action taken"""
        b_prime = np.zeros(b.shape)
        btwin_prime = np.zeros(btwin.shape) 
        beps_prime = beps.copy()
        ob = np.zeros(self.n_comp)
        drate_prime = np.zeros((self.n_comp, 1), dtype=int)
        for i in range(self.n_comp):
            ob[i] = 2*self.n_st_stress
            # twin_state = np.nonzero(np.random.multinomial(1, btwin[i,:]))[0][0]
            twin_state = np.nonzero(btwin[i,:])[0]
            # TRANSITION THE PHYSICAL TWIN
            if a[i] == 4 or a[i] == 5: # Perfect-repair                
                b_prime[i, :] = b[i, :].dot(self.Tr[drate[i, 0]]) 
                drate_prime[i, 0] = 0                 
                          
            else:   # Do-nothing
                p1 = b[i, :].dot(self.T0[drate[i, 0]])
                drate_prime[i, 0] = drate[i, 0] + 1     
                if a[i] == 0 or a[i] == 2: # No-inspection
                    if twin_state == 0: # Belief update with load observation from physical sensor
                        prob_obs = self.O_monitor.T.dot(p1)
                        s1 = np.nonzero(np.random.multinomial(1, prob_obs))[0][0]
                        b_prime[i, :] = p1*self.O_monitor[:,s1]/sum(p1*self.O_monitor[:,s1])    
                        ob[i] = s1
                    elif twin_state == 1: # Belief update with load observation from virtual sensor
                        # Built the observation model on the go
                        qobs, epsilon = self.dtwin_observation_matrix(beps[i,:])
                        O = np.zeros(self.O_monitor.shape)
                        O[:,0:self.n_st_stress] = qobs 
                        prob_obs = O.T.dot(p1)
                        s1 = np.nonzero(np.random.multinomial(1, prob_obs))[0][0]
                        b_prime[i, :] = p1*O[:,s1]/sum(p1*O[:,s1])    
                        beps_prime[i,0] = epsilon # The second parameter controls how uncertain the turbine will evolve
                        ob[i] = s1 
                    else: # No belief update
                        b_prime[i, :] = p1
                    
                elif a[i] == 1 or a[i] == 3: # Inspection
                    if twin_state == 0:# Belief update with load observation from physical sensor and inspection
                        prob_obs = self.O_ins_monitor.T.dot(p1)
                        s1 = np.nonzero(np.random.multinomial(1, prob_obs))[0][0]
                        b_prime[i, :] = p1*self.O_ins_monitor[:,s1]/sum(p1*self.O_ins_monitor[:,s1]) 
                        ob[i] = s1 
                    elif twin_state == 1: # Belief update with load observation from virtual sensor and inspection
                        # Built the observation model on the go
                        qobs, epsilon = self.dtwin_observation_matrix(beps[i,:])
                        O = np.zeros(self.O_ins_monitor.shape)
                        O = np.concatenate((qobs.T*(self.O_ins[:,0]), qobs.T*(self.O_ins[:,self.n_st_stress])),axis=0).T
                        prob_obs = O.T.dot(p1)
                        s1 = np.nonzero(np.random.multinomial(1, prob_obs))[0][0]
                        b_prime[i, :] = p1*O[:,s1]/sum(p1*O[:,s1]) 
                        beps_prime[i,0] = epsilon # The second parameter controls how uncertain the turbine will evolve
                        ob[i] = s1 
                    else: # Belief update with inspection
                        prob_obs = self.O_ins.T.dot(p1)
                        s1 = np.nonzero(np.random.multinomial(1, prob_obs))[0][0]
                        b_prime[i, :] = p1*self.O_ins[:,s1]/sum(p1*self.O_ins[:,s1]) 
                        ob[i] = s1 
           
            # TRANSITION THE DIGITAL  TWIN  
            if a[i] == 4: # Perfect repair (Go back to no physical/virtual sensor)
                btwin_prime[i,:] = btwin[i,:].dot(self.Tr_twin)
                beps_prime[i,0] = 0.1
            elif a[i] == 0 or a[i] ==1:  
                # btwin_prime[i,twin_state] = 1 # The digital twin belief becomes the fully-observed state
                btwin_prime[i,:] = btwin[i,:].dot(self.T0_twin) # Transition the digital twin belief stochastically
            else:  # Install sensor
                btwin_prime[i,:] = btwin[i,:].dot(self.Ts_twin)
                beps_prime[i,0] = 0.1
        return ob, b_prime, btwin_prime, beps_prime, drate_prime

    def dtwin_observation_matrix(self, beps):
        epsilon = np.random.normal(beps[0],beps[0]*beps[1],(1,1))
        while epsilon < 0:
            epsilon = np.random.normal(beps[0],beps[0]*beps[1],(1,1))
        # qobs_std = np.ones((100,))*epsilon          
        qobs = np.zeros((self.n_st_stress, self.n_st_stress))
        for k in range(self.n_st_stress):
            qobs_mean = np.linspace(self.q_interv[k],self.q_interv[k+1],100)
            #Negative samples are taken as first bin q_ref[0] = -1e100
            qobs_cdf = stats.norm.cdf(self.q_ref, qobs_mean, (0.07+epsilon)*qobs_mean).T
            qobs_pdf = np.diff(qobs_cdf)/100
            qobs[k,:] += np.sum(qobs_pdf, axis=0) 
        qobs = np.tile(qobs,(self.n_st_comp,1))
        return qobs, epsilon