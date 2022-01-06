# RAFT's main model class

import os
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import cm
import yaml

import moorpy as mp
import raft.raft_fowt  as fowt
from raft.helpers import *

#import F6T1RNA as structural    # import turbine structural model functions

raft_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TwoPi = 2.0*np.pi

class Model():


    def __init__(self, design, nTurbines=1):
        '''
        Empty frequency domain model initialization function

        design : dict
            Dictionary of all the design info from turbine to platform to moorings
        nTurbines
            could in future be used to set up any number of identical turbines
        '''

        self.fowtList = []
        self.coords = []

        self.nDOF = 0  # number of DOFs in system

        self.design = design # save design dictionary for possible later use/reference


        # parse settings
        if not 'settings' in design:    # if settings field not in input data
            design['settings'] = {}     # make an empty one to avoid errors
        
        min_freq     = getFromDict(design['settings'], 'min_freq', default=0.01, dtype=float)  # [Hz] lowest frequency to consider, also the frequency bin width 
        max_freq     = getFromDict(design['settings'], 'max_freq', default=1.00, dtype=float)  # [Hz] highest frequency to consider
        self.XiStart = getFromDict(design['settings'], 'XiStart' , default=0.1 , dtype=float)  # sets initial amplitude of each DOF for all frequencies
        self.nIter   = getFromDict(design['settings'], 'nIter'   , default=15  , dtype=int  )  # sets how many iterations to perform in Model.solveDynamics()
        
        self.w = np.arange(min_freq, max_freq+0.5*min_freq, min_freq) *2*np.pi  # angular frequencies to analyze (rad/s)
        self.nw = len(self.w)  # number of frequencies
                
        
        # process mooring information 
        self.ms = mp.System()
        self.ms.parseYAML(design['mooring'])
        
        # depth and wave number        
        self.depth = getFromDict(design['site'], 'water_depth', dtype=float)
        self.k = np.zeros(self.nw)  # wave number
        for i in range(self.nw):
            self.k[i] = waveNumber(self.w[i], self.depth)
        
        # set up the FOWT here  <<< only set for 1 FOWT for now <<<
        self.fowtList.append(fowt.FOWT(design, self.w, self.ms.bodyList[0], depth=self.depth))
        self.coords.append([0.0,0.0])
        self.nDOF += 6

        self.ms.bodyList[0].type = -1  # need to make sure it's set to a coupled type

        try:
            self.ms.initialize()  # reinitialize the mooring system to ensure all things are tallied properly etc.
        except Exception as e:
            raise RuntimeError('An error occured when initializing the mooring system: '+e.message)
        
        self.results = {}     # dictionary to hold all results from the model
        


    def addFOWT(self, fowt, xy0=[0,0]):
        '''(not used currently) Adds an already set up FOWT to the frequency domain model solver.'''

        self.fowtList.append(fowt)
        self.coords.append(xy0)
        self.nDOF += 6

        # would potentially need to add a mooring system body for it too <<<


    """
    def setEnv(self, Hs=8, Tp=12, spectrum='unit', V=10, beta=0, Fthrust=0):

        self.env = Env()
        self.env.Hs       = Hs
        self.env.Tp       = Tp
        self.env.spectrum = spectrum
        self.env.V        = V
        self.env.beta     = beta
        self.Fthrust      = Fthrust

        for fowt in self.fowtList:
            fowt.setEnv(Hs=Hs, Tp=Tp, V=V, spectrum=spectrum, beta=beta, Fthrust=Fthrust)
    """


    def analyzeUnloaded(self):
        '''This calculates the system properties under undloaded coonditions: equilibrium positions, natural frequencies, etc.'''

        # calculate the system's constant properties
        #self.calcSystemConstantProps()
        for fowt in self.fowtList:
            fowt.calcStatics()
            #fowt.calcBEM()
            fowt.calcHydroConstants(dict(wave_spectrum='still', wave_heading=0))
            
        # get mooring system characteristics about undisplaced platform position (useful for baseline and verification)
        try: 
            self.C_moor0 = self.ms.getCoupledStiffness(lines_only=True)                             # this method accounts for eqiuilibrium of free objects in the system
            self.F_moor0 = self.ms.getForces(DOFtype="coupled", lines_only=True)
        except Exception as e:
            raise RuntimeError('An error occured when getting linearized mooring properties in undisplaced state: '+e.message)

        self.results['properties'] = {}   # signal this data is available by adding a section to the results dictionary
            
        # calculate platform offsets and mooring system equilibrium state
        self.calcMooringAndOffsets()
        self.results['properties']['offset_unloaded'] = self.fowtList[0].Xi0

        # TODO: add printing of summary info here - mass, stiffnesses, etc
        
    
    def analyzeCases(self, display=0):
        '''This runs through all the specified load cases, building a dictionary of results.'''
        
        nCases = len(self.design['cases']['data'])
        nLines = len(self.ms.lineList)        
        
        
        # set up output arrays for load cases
        
        self.results['case_metrics'] = {}
        self.results['case_metrics']['surge_avg'] = np.zeros(nCases)
        self.results['case_metrics']['surge_std'] = np.zeros(nCases)
        self.results['case_metrics']['surge_max'] = np.zeros(nCases)
        self.results['case_metrics']['surge_PSD'] = np.zeros([nCases,self.nw])  # adding PSDs as well. Could put behind an if statement if this slows things down
        
        self.results['case_metrics']['sway_avg'] = np.zeros(nCases)
        self.results['case_metrics']['sway_std'] = np.zeros(nCases)
        self.results['case_metrics']['sway_max'] = np.zeros(nCases)
        self.results['case_metrics']['sway_PSD'] = np.zeros([nCases,self.nw])
        
        self.results['case_metrics']['heave_avg'] = np.zeros(nCases)
        self.results['case_metrics']['heave_std'] = np.zeros(nCases)
        self.results['case_metrics']['heave_max'] = np.zeros(nCases)
        self.results['case_metrics']['heave_PSD'] = np.zeros([nCases,self.nw])
        
        self.results['case_metrics']['roll_avg'] = np.zeros(nCases)
        self.results['case_metrics']['roll_std'] = np.zeros(nCases)
        self.results['case_metrics']['roll_max'] = np.zeros(nCases)
        self.results['case_metrics']['roll_PSD'] = np.zeros([nCases,self.nw])
        
        self.results['case_metrics']['pitch_avg'] = np.zeros(nCases)
        self.results['case_metrics']['pitch_std'] = np.zeros(nCases)
        self.results['case_metrics']['pitch_max'] = np.zeros(nCases)
        self.results['case_metrics']['pitch_PSD'] = np.zeros([nCases,self.nw])
        
        self.results['case_metrics']['yaw_avg'] = np.zeros(nCases)
        self.results['case_metrics']['yaw_std'] = np.zeros(nCases)
        self.results['case_metrics']['yaw_max'] = np.zeros(nCases)
        self.results['case_metrics']['yaw_PSD'] = np.zeros([nCases,self.nw])
        
        # nacelle acceleration
        self.results['case_metrics']['AxRNA_avg'] = np.zeros(nCases)
        self.results['case_metrics']['AxRNA_std'] = np.zeros(nCases)
        self.results['case_metrics']['AxRNA_max'] = np.zeros(nCases)
        self.results['case_metrics']['AxRNA_PSD'] = np.zeros([nCases,self.nw])
        # tower base bending moment
        self.results['case_metrics']['Mbase_avg'] = np.zeros(nCases) 
        self.results['case_metrics']['Mbase_std'] = np.zeros(nCases)
        self.results['case_metrics']['Mbase_max'] = np.zeros(nCases)
        self.results['case_metrics']['Mbase_PSD'] = np.zeros([nCases,self.nw])
        self.results['case_metrics']['Mbase_DEL'] = np.zeros(nCases)        
        # rotor speed
        self.results['case_metrics']['omega_avg'] = np.zeros(nCases)    
        self.results['case_metrics']['omega_std'] = np.zeros(nCases)    
        self.results['case_metrics']['omega_max'] = np.zeros(nCases)   
        self.results['case_metrics']['omega_PSD'] = np.zeros([nCases,self.nw])        
        # generator torque
        self.results['case_metrics']['torque_avg'] = np.zeros(nCases) 
        self.results['case_metrics']['torque_std'] = np.zeros(nCases)    
        self.results['case_metrics']['torque_max'] = np.zeros(nCases)  
        self.results['case_metrics']['torque_PSD'] = np.zeros([nCases,self.nw])        
        # rotor power 
        self.results['case_metrics']['power_avg'] = np.zeros(nCases)
        self.results['case_metrics']['power_std'] = np.zeros(nCases)    
        self.results['case_metrics']['power_max'] = np.zeros(nCases)   
        self.results['case_metrics']['power_PSD'] = np.zeros([nCases,self.nw])        
        # collective blade pitch
        self.results['case_metrics']['bPitch_avg'] = np.zeros(nCases)   
        self.results['case_metrics']['bPitch_std'] = np.zeros(nCases)    
        self.results['case_metrics']['bPitch_max'] = np.zeros(nCases) 
        self.results['case_metrics']['bPitch_PSD'] = np.zeros([nCases, self.nw])
        # mooring tension
        self.results['case_metrics']['Tmoor_avg'] = np.zeros([nCases, 2*nLines]) # 2d array, for each line in each case?
        self.results['case_metrics']['Tmoor_std'] = np.zeros([nCases, 2*nLines])
        self.results['case_metrics']['Tmoor_max'] = np.zeros([nCases, 2*nLines])
        self.results['case_metrics']['Tmoor_DEL'] = np.zeros([nCases, 2*nLines])
        self.results['case_metrics']['Tmoor_PSD'] = np.zeros([nCases, 2*nLines, self.nw])
        
        # wind and wave spectra for reference
        self.results['case_metrics']['wind_PSD'] = np.zeros([nCases, self.nw])
        self.results['case_metrics']['wave_PSD'] = np.zeros([nCases, self.nw])
        
        # calculate the system's constant properties
        for fowt in self.fowtList:
            fowt.calcStatics()
            fowt.calcBEM()
            
        # loop through each case
        for iCase in range(nCases):
        
            print(f"\n--------------------- Running Case {iCase+1} ----------------------")
            print(self.design['cases']['data'][iCase])
        
            # form dictionary of case parameters
            case = dict(zip( self.design['cases']['keys'], self.design['cases']['data'][iCase]))   

            # get initial FOWT values assuming no offset
            for fowt in self.fowtList:
                fowt.Xi0 = np.zeros(6)      # zero platform offsets
                fowt.calcTurbineConstants(case, ptfm_pitch=0.0)
                fowt.calcHydroConstants(case)
            
            # calculate platform offsets and mooring system equilibrium state
            self.calcMooringAndOffsets()
            
            # update values based on offsets if applicable
            for fowt in self.fowtList:
                fowt.calcTurbineConstants(case, ptfm_pitch=fowt.Xi0[4])
                # fowt.calcHydroConstants(case)  (hydrodynamics don't account for offset, so far)
            
            # (could solve mooring and offsets a second time, but likely overkill)
            
            # solve system dynamics
            self.solveDynamics(case)
            
            # process outputs that are specific to the floating unit       
            self.fowtList[0].saveTurbineOutputs(self.results['case_metrics'], case, iCase, fowt.Xi0, self.Xi[0:6,:])            
 
            # process mooring tension outputs
            nLine = int(len(self.T_moor)/2)
            T_moor_amps = np.zeros([2*nLine, self.nw], dtype=complex) 
            for iw in range(self.nw):
                T_moor_amps[:,iw] = np.matmul(self.J_moor, self.Xi[:,iw])   # FFT of mooring tensions
            
            self.results['case_metrics']['Tmoor_avg'][iCase,:] = self.T_moor
            for iT in range(2*nLine):
                TRMS = getRMS(T_moor_amps[iT,:], self.w[0]) # estimated mooring line RMS tension [N]
                self.results['case_metrics']['Tmoor_std'][iCase,iT] = TRMS
                self.results['case_metrics']['Tmoor_max'][iCase,iT] = self.T_moor[iT] + 3*TRMS
                self.results['case_metrics']['Tmoor_PSD'][iCase,iT,:] = getPSD(T_moor_amps[iT,:]) # PSD in N^2/(rad/s)
                #self.results['case_metrics']['Tmoor_DEL'][iCase,iT] = 
        
            if display > 0:
        
                metrics = self.results['case_metrics']
            
                # print statistics table
                print(f"-------------------- Case {iCase+1} Statistics --------------------")
                print("Response channel     Average     RMS         Maximum")
                print(f"surge (m)          {metrics['surge_avg'][iCase] :10.2e}  {metrics['surge_std'][iCase] :10.2e}  {metrics['surge_max'][iCase] :10.2e}")
                print(f"sway (m)           {metrics['sway_avg' ][iCase] :10.2e}  {metrics['sway_std' ][iCase] :10.2e}  {metrics['sway_max' ][iCase] :10.2e}")
                print(f"heave (m)          {metrics['heave_avg'][iCase] :10.2e}  {metrics['heave_std'][iCase] :10.2e}  {metrics['heave_max'][iCase] :10.2e}")
                print(f"roll (deg)         {metrics['roll_avg' ][iCase] :10.2e}  {metrics['roll_std' ][iCase] :10.2e}  {metrics['roll_max' ][iCase] :10.2e}")
                print(f"pitch (deg)        {metrics['pitch_avg'][iCase] :10.2e}  {metrics['pitch_std'][iCase] :10.2e}  {metrics['pitch_max'][iCase] :10.2e}")
                print(f"yaw (deg)          {metrics[  'yaw_avg'][iCase] :10.2e}  {metrics[  'yaw_std'][iCase] :10.2e}  {metrics['yaw_max'  ][iCase] :10.2e}")
                print(f"nacelle acc. (m/s) {metrics['AxRNA_avg'][iCase] :10.2e}  {metrics['AxRNA_std'][iCase] :10.2e}  {metrics['AxRNA_max'][iCase] :10.2e}")
                print(f"tower bending (Nm) {metrics['Mbase_avg'][iCase] :10.2e}  {metrics['Mbase_std'][iCase] :10.2e}  {metrics['Mbase_max'][iCase] :10.2e}")
                
                print(f"rotor speed (RPM)  {metrics['omega_avg'][iCase] :10.2e}  {metrics['omega_std'][iCase] :10.2e}  {metrics['omega_max'][iCase] :10.2e}")
                print(f"blade pitch (deg)  {metrics['bPitch_avg'][iCase] :10.2e}  {metrics['bPitch_std'][iCase] :10.2e} ")
                print(f"rotor power        {metrics['power_avg'][iCase] :10.2e} ")
                for i in range(nLine):
                    j = i+nLine
                    #print(f"line {i} tension A  {metrics['Tmoor_avg'][iCase,i]:10.2e}  {metrics['Tmoor_std'][iCase,i]:10.2e}  {metrics['Tmoor_max'][iCase,i]:10.2e}")
                    print(f"line {i} tension (N) {metrics['Tmoor_avg'][iCase,j]:10.2e}  {metrics['Tmoor_std'][iCase,j]:10.2e}  {metrics['Tmoor_max'][iCase,j]:10.2e}")
                print(f"-----------------------------------------------------------")

        

    """
    def calcSystemConstantProps(self):
        '''This gets the various static/constant calculations of each FOWT done. (Those that don't depend on load case.)'''

        for fowt in self.fowtList:
            fowt.calcBEM()
            fowt.calcStatics()
            #fowt.calcDynamicConstants()
        
        # First get mooring system characteristics about undisplaced platform position (useful for baseline and verification)
        try:
            self.C_moor0 = self.ms.getCoupledStiffness(lines_only=True)                             # this method accounts for eqiuilibrium of free objects in the system
            self.F_moor0 = self.ms.getForces(DOFtype="coupled", lines_only=True)
        except Exception as e:
            raise RuntimeError('An error occured when getting linearized mooring properties in undisplaced state: '+e.message)
        
        self.results['properties'] = {}   # signal this data is available by adding a section to the results dictionary
    """    
    
    def calcMooringAndOffsets(self):
        '''Calculates mean offsets and linearized mooring properties for the current load case.
        setEnv and calcSystemProps must be called first.  This will ultimately become a method for solving mean operating point.
        '''

        # apply any mean aerodynamic and hydrodynamic loads
        F_PRP = self.fowtList[0].F_aero0# + self.fowtList[0].F_hydro0 <<< hydro load would be nice here eventually
        self.ms.bodyList[0].f6Ext = np.array(F_PRP)


        # Now find static equilibrium offsets of platform and get mooring properties about that point
        # (This assumes some loads have been applied)
        #self.ms.display=2

        try:
            self.ms.solveEquilibrium3(DOFtype="both", tol=0.01) #, rmsTol=1.0E-5)     # get the system to its equilibrium
        except Exception as e:     #mp.MoorPyError
            print('An error occured when solving system equilibrium: '+e.message)
            #raise RuntimeError('An error occured when solving unloaded equilibrium: '+error.message)
            
        # ::: a loop could be added here for an array :::
        fowt = self.fowtList[0]
        
        #print("Equilibrium'3' platform positions/rotations:")
        #printVec(self.ms.bodyList[0].r6)

        r6eq = self.ms.bodyList[0].r6
        fowt.Xi0 = np.array(r6eq)   # save current mean offsets for the FOWT

        #self.ms.plot()

        print(f"Found mean offets with with surge = {r6eq[0]:.2f} m and pitch = {r6eq[4]*180/np.pi:.2f} deg.")

        try:
            C_moor, J_moor = self.ms.getCoupledStiffness(lines_only=True, tensions=True) # get stiffness matrix and tension jacobian matrix
            F_moor = self.ms.getForces(DOFtype="coupled", lines_only=True)    # get net forces and moments from mooring lines on Body
            T_moor = self.ms.getTensions()
        except Exception as e:
            raise RuntimeError('An error occured when getting linearized mooring properties in offset state: '+e.message)
            
        # add any additional yaw stiffness that isn't included in the MoorPy model (e.g. if a bridle isn't modeled)
        C_moor[5,5] += fowt.yawstiff

        self.C_moor = C_moor
        self.J_moor = J_moor        # jacobian of mooring line tensions w.r.t. coupled DOFs
        self.F_moor = F_moor
        self.T_moor = T_moor

        # store results
        self.results['means'] = {}   # signal this data is available by adding a section to the results dictionary
        self.results['means']['aero force'  ] = self.fowtList[0].F_aero0
        self.results['means']['platform offset'  ] = r6eq
        self.results['means']['mooring force'    ] = F_moor
        self.results['means']['fairlead tensions'] = np.array([np.linalg.norm(self.ms.pointList[id-1].getForces()) for id in self.ms.bodyList[0].attachedP])
        
    
    

    def solveEigen(self):
        '''finds natural frequencies of system'''


        # total system coefficient arrays
        M_tot = np.zeros([self.nDOF,self.nDOF])       # total mass and added mass matrix [kg, kg-m, kg-m^2]
        C_tot = np.zeros([self.nDOF,self.nDOF])       # total stiffness matrix [N/m, N, N-m]

        # add in mooring stiffness from MoorPy system
        C_tot += np.array(self.C_moor0)

        # ::: a loop could be added here for an array :::
        fowt = self.fowtList[0]

        # add any additional yaw stiffness that isn't included in the MoorPy model (e.g. if a bridle isn't modeled)
        C_tot[5,5] += fowt.yawstiff     # will need to be put in calcSystemProps() once there is more than 1 fowt in a model

        # add fowt's terms to system matrices (BEM arrays are not yet included here)
        M_tot += fowt.M_struc + fowt.A_hydro_morison   # mass
        C_tot += fowt.C_struc + fowt.C_hydro           # stiffness

        # check viability of matrices
        message=''
        for i in range(self.nDOF):
            if M_tot[i,i] < 1.0:
                message += f'Diagonal entry {i} of system mass matrix is less than 1 ({M_tot[i,i]}). '
            if C_tot[i,i] < 1.0:
                message += f'Diagonal entry {i} of system stiffness matrix is less than 1 ({C_tot[i,i]}). '
                
        if len(message) > 0:
            raise RuntimeError('System matrices computed by RAFT have one or more small or negative diagonals: '+message)

        # calculate natural frequencies (using eigen analysis to get proper values for pitch and roll - otherwise would need to base about CG if using diagonal entries only)
        eigenvals, eigenvectors = np.linalg.eig(np.matmul(np.linalg.inv(M_tot), C_tot))   # <<< need to sort this out so it gives desired modes, some are currently a bit messy

        if any(eigenvals <= 0.0):
            raise RuntimeError("Error: zero or negative system eigenvalues detected.")

        # sort to normal DOF order based on which DOF is largest in each eigenvector
        ind_list = []
        for i in range(5,-1, -1):
            vec = np.abs(eigenvectors[i,:])  # look at each row (DOF) at a time (use reverse order to pick out rotational DOFs first)

            for j in range(6):               # now do another loop in case the index was claimed previously

                ind = np.argmax(vec)         # find the index of the vector with the largest value of the current DOF

                if ind in ind_list:          # if a previous vector claimed this DOF, set it to zero in this vector so that we look at the other vectors
                    vec[ind] = 0.0
                else:
                    ind_list.append(ind)     # if it hasn't been claimed before, assign this vector to the DOF
                    break

        ind_list.reverse()   # reverse the index list since we made it in reverse order

        fns = np.sqrt(eigenvals[ind_list])/2.0/np.pi   # apply sorting to eigenvalues and convert to natural frequency in Hz
        modes = eigenvectors[:,ind_list]               # apply sorting to eigenvectors

        print("")
        print("--------- Natural frequencies and mode shapes -------------")
        print("Mode        1         2         3         4         5         6")
        print("Fn (Hz)"+"".join([f"{fn:10.4f}" for fn in fns]))
        print("")
        for i in range(6):
            print(f"DOF {i+1}  "+"".join([f"{modes[i,j]:10.4f}" for j in range(6)]))
        print("-----------------------------------------------------------")
        
        '''
        print("natural frequencies from eigen values")
        printVec(fns)
        print(1/fns)
        print("mode shapes from eigen values")
        printMat(modes)
        
        # alternative attempt to calculate natural frequencies based on diagonal entries (and taking pitch and roll about CG)
        if C_tot[0,0] == 0.0:
            zMoorx = 0.0
        else:
            zMoorx = C_tot[0,4]/C_tot[0,0]  # effective z elevation of mooring system reaction forces in x and y directions

        if C_tot[1,1] == 0.0:
            zMoory = 0.0
        else:
            zMoory = C_tot[1,3]/C_tot[1,1]

        zCG  = fowt.rCG_TOT[2]                    # center of mass in z
        zCMx = M_tot[0,4]/M_tot[0,0]              # effective z elevation of center of mass and added mass in x and y directions
        zCMy = M_tot[1,3]/M_tot[1,1]

        print("natural frequencies with added mass")
        fn = np.zeros(6)
        fn[0] = np.sqrt( C_tot[0,0] / M_tot[0,0] )/ 2.0/np.pi
        fn[1] = np.sqrt( C_tot[1,1] / M_tot[1,1] )/ 2.0/np.pi
        fn[2] = np.sqrt( C_tot[2,2] / M_tot[2,2] )/ 2.0/np.pi
        fn[5] = np.sqrt( C_tot[5,5] / M_tot[5,5] )/ 2.0/np.pi
        fn[3] = np.sqrt( (C_tot[3,3] + C_tot[1,1]*((zCMy-zMoory)**2 - zMoory**2) ) / (M_tot[3,3] - M_tot[1,1]*zCMy**2 ))/ 2.0/np.pi     # this contains adjustments to reflect rotation about the CG rather than PRP
        fn[4] = np.sqrt( (C_tot[4,4] + C_tot[0,0]*((zCMx-zMoorx)**2 - zMoorx**2) ) / (M_tot[4,4] - M_tot[0,0]*zCMx**2 ))/ 2.0/np.pi     # this contains adjustments to reflect rotation about the CG rather than PRP
        # note that the above lines use off-diagonal term rather than parallel axis theorem since rotation will not be exactly at CG due to effect of added mass
        printVec(fn)
        print(1/fn)
        '''
                
        # store results
        self.results['eigen'] = {}   # signal this data is available by adding a section to the results dictionary
        self.results['eigen']['frequencies'] = fns
        self.results['eigen']['modes'      ] = modes
  

    def solveDynamics(self, case, tol=0.01, conv_plot=0, RAO_plot=0):
        '''After all constant parts have been computed, call this to iterate through remaining terms
        until convergence on dynamic response. Note that steady/mean quantities are excluded here.

        nIter = 2  # maximum number of iterations to allow
        '''
        
        nIter = int(self.nIter) + 1         # maybe think of a better name for the first nIter
        XiStart = self.XiStart
        
        # total system complex response amplitudes (this gets updated each iteration)
        XiLast = np.zeros([self.nDOF,self.nw], dtype=complex) + XiStart    # displacement and rotation complex amplitudes [m, rad]
        
        if conv_plot:
            fig, ax = plt.subplots(3,1,sharex=True)
            c = np.arange(nIter+1)      # adding 1 again here so that there are no RuntimeErrors
            c = cm.jet((c-np.min(c))/(np.max(c)-np.min(c)))      # set up colormap to use to plot successive iteration results

        # ::: a loop could be added here for an array :::
        fowt = self.fowtList[0]
        i1 = 0                                                # range of DOFs for the current turbine
        i2 = 6
        
        # TEMPORARY <<<<
        #fowt.B_aero[0,4,:] = 0.0
        #fowt.B_aero[4,0,:] = 0.0

        # sum up all linear (non-varying) matrices up front
        M_lin = fowt.A_aero + fowt.M_struc[:,:,None] + fowt.A_BEM + fowt.A_hydro_morison[:,:,None] # mass
        B_lin = fowt.B_aero + fowt.B_struc[:,:,None] + fowt.B_BEM                                  # damping
        C_lin =               fowt.C_struc   + self.C_moor        + fowt.C_hydro                   # stiffness
        F_lin = fowt.F_aero +                          fowt.F_BEM + fowt.F_hydro_iner              # excitation
        
        # start fixed point iteration loop for dynamics
        for iiter in range(nIter):
            
            # initialize/zero total system coefficient arrays
            M_tot = np.zeros([self.nDOF,self.nDOF,self.nw])       # total mass and added mass matrix [kg, kg-m, kg-m^2]
            B_tot = np.zeros([self.nDOF,self.nDOF,self.nw])       # total damping matrix [N-s/m, N-s, N-s-m]
            C_tot = np.zeros([self.nDOF,self.nDOF,self.nw])       # total stiffness matrix [N/m, N, N-m]
            F_tot = np.zeros([self.nDOF,self.nw], dtype=complex)  # total excitation force/moment complex amplitudes vector [N, N-m]

            Z  = np.zeros([self.nDOF,self.nDOF,self.nw], dtype=complex)  # total system impedance matrix


            # a loop could be added here for an array
            fowt = self.fowtList[0]
            
            # get linearized terms for the current turbine given latest amplitudes
            B_linearized, F_linearized = fowt.calcLinearizedTerms(XiLast)
            
            # calculate the response based on the latest linearized terms
            Xi = np.zeros([self.nDOF,self.nw], dtype=complex)     # displacement and rotation complex amplitudes [m, rad]

            # add fowt's terms to system matrices (BEM arrays are not yet included here)
            M_tot[:,:,:] = M_lin
            B_tot[:,:,:] = B_lin           + B_linearized[:,:,None]
            C_tot[:,:,:] = C_lin[:,:,None]
            F_tot[:  ,:] = F_lin           + F_linearized


            for ii in range(self.nw):
                # form impedance matrix
                Z[:,:,ii] = -self.w[ii]**2 * M_tot[:,:,ii] + 1j*self.w[ii]*B_tot[:,:,ii] + C_tot[:,:,ii]
                
                # solve response (complex amplitude)
                Xi[:,ii] = np.matmul(np.linalg.inv(Z[:,:,ii]),  F_tot[:,ii] )


            if conv_plot:
                # Convergence Plotting
                # plots of surge response at each iteration for observing convergence
                ax[0].plot(self.w, np.abs(Xi[0,:]) , color=c[iiter], label=f"iteration {iiter}")
                ax[1].plot(self.w, np.real(Xi[0,:]), color=c[iiter], label=f"iteration {iiter}")
                ax[2].plot(self.w, np.imag(Xi[0,:]), color=c[iiter], label=f"iteration {iiter}")
    
            # check for convergence
            tolCheck = np.abs(Xi - XiLast) / ((np.abs(Xi)+tol))
            if (tolCheck < tol).all():
                print(f" Iteration {iiter}, converged (largest change is {np.max(tolCheck):.5f} < {tol})")
                break
            else:
                XiLast = 0.2*XiLast + 0.8*Xi    # use a mix of the old and new response amplitudes to use for the next iteration
                                                # (uses hard-coded successive under relaxation for now)
                print(f" Iteration {iiter}, unconverged (largest change is {np.max(tolCheck):.5f} >= {tol})")
    
            if iiter == nIter-1:
                print("WARNING - solveDynamics iteration did not converge to the tolerance.")
        
        if conv_plot:
            # labels for convergence plots
            ax[1].legend()
            ax[0].set_ylabel("response magnitude")
            ax[1].set_ylabel("response, real")
            ax[2].set_ylabel("response, imag")
            ax[2].set_xlabel("frequency (rad/s)")
            fig.suptitle("Response convergence")


        # ------------------------------ preliminary plotting of response ---------------------------------
        
        if RAO_plot:
            # RAO plotting
            fig, ax = plt.subplots(3,1, sharex=True)
    
            fowt = self.fowtList[0]
    
            ax[0].plot(self.w, np.abs(Xi[0,:])          , 'b', label="surge")
            ax[0].plot(self.w, np.abs(Xi[1,:])          , 'g', label="sway")
            ax[0].plot(self.w, np.abs(Xi[2,:])          , 'r', label="heave")
            ax[1].plot(self.w, np.abs(Xi[3,:])*180/np.pi, 'b', label="roll")
            ax[1].plot(self.w, np.abs(Xi[4,:])*180/np.pi, 'g', label="pitch")
            ax[1].plot(self.w, np.abs(Xi[5,:])*180/np.pi, 'r', label="yaw")
            ax[2].plot(self.w, fowt.zeta,                 'k', label="wave amplitude (m)")
    
            ax[0].legend()
            ax[1].legend()
            ax[2].legend()
    
            #ax[0].set_ylim([0, 1e6])
            #ax[1].set_ylim([0, 1e9])
    
            ax[0].set_ylabel("response magnitude (m)")
            ax[1].set_ylabel("response magnitude (deg)")
            ax[2].set_ylabel("wave amplitude (m)")
            ax[2].set_xlabel("frequency (rad/s)")

        
        self.Xi = Xi

        self.results['response'] = {}   # signal this data is available by adding a section to the results dictionary

        return Xi  # currently returning the response rather than saving in the model object



    def calcOutputs(self):
        '''This is where various output quantities of interest are calculated based on the already-solved system response.'''
        
        fowt = self.fowtList[0]   # just using a single turbine for now
        
        
        # ----- system properties outputs -----------------------------
        # all values about platform reference point (z=0) unless otherwise noted
        
        if 'properties' in self.results:
        
            self.results['properties']['tower mass'] = fowt.mtower
            self.results['properties']['tower CG'] = fowt.rCG_tow
            self.results['properties']['substructure mass'] = fowt.msubstruc
            self.results['properties']['substructure CG'] = fowt.rCG_sub
            self.results['properties']['shell mass'] = fowt.mshell
            self.results['properties']['ballast mass'] = fowt.mballast
            self.results['properties']['ballast densities'] = fowt.pb
            self.results['properties']['total mass'] = fowt.M_struc[0,0]
            self.results['properties']['total CG'] = fowt.rCG_TOT
            #self.results['properties']['roll inertia at subCG'] = fowt.I44
            #self.results['properties']['pitch inertia at subCG'] = fowt.I55
            #self.results['properties']['yaw inertia at subCG'] = fowt.I66
            self.results['properties']['roll inertia at subCG'] = fowt.M_struc_subCM[3,3]
            self.results['properties']['pitch inertia at subCG'] = fowt.M_struc_subCM[4,4]
            self.results['properties']['yaw inertia at subCG'] = fowt.M_struc_subCM[5,5]
            
            self.results['properties']['Buoyancy (pgV)'] = fowt.rho_water*fowt.g*fowt.V
            self.results['properties']['Center of Buoyancy'] = fowt.rCB
            self.results['properties']['C stiffness matrix'] = fowt.C_hydro
            
            # unloaded equilibrium <<< 
            
            self.results['properties']['F_lines0'] = self.F_moor0
            self.results['properties']['C_lines0'] = self.C_moor0
                    
            # 6DOF matrices for the support structure (everything but turbine) including mass, hydrostatics, and mooring reactions
            self.results['properties']['M support structure'] = fowt.M_struc_subCM                          # mass matrix
            self.results['properties']['A support structure'] = fowt.A_hydro_morison + fowt.A_BEM[:,:,-1]   # hydrodynamic added mass (currently using highest frequency of BEM added mass)
            self.results['properties']['C support structure'] = fowt.C_struc_sub + fowt.C_hydro + self.C_moor0  # stiffness

        
        
        # ----- response outputs (always in standard units) ---------------------------------------
        
        if 'response' in self.results:
            
            RAOmag      = abs(self.Xi          /fowt.zeta)  # magnitudes of motion RAO

            self.results['response']['frequencies'] = self.w/2/np.pi         # Hz
            self.results['response']['wave elevation'] = fowt.zeta
            self.results['response']['Xi'         ] = self.Xi
            self.results['response']['surge RAO'  ] = RAOmag[0,:]
            self.results['response'][ 'sway RAO'  ] = RAOmag[1,:]
            self.results['response']['heave RAO'  ] = RAOmag[2,:]
            self.results['response']['pitch RAO'  ] = RAOmag[3,:]
            self.results['response'][ 'roll RAO'  ] = RAOmag[4,:]
            self.results['response'][  'yaw RAO'  ] = RAOmag[5,:]
            
            # save dynamic derived quantities
            #self.results['response']['mooring tensions'] = ...
            self.results['response']['nacelle acceleration'] = self.w**2 * (self.Xi[0] + self.Xi[4]*fowt.hHub)
        
    
        
        return self.results


    def plotResponses(self):
        '''Plots the power spectral densities of the available response channels for each case.'''
        
        fig, ax = plt.subplots(6, 1, sharex=True)
        
        metrics = self.results['case_metrics']
        nCases = len(metrics['surge_avg'])
        
        for iCase in range(nCases):
        
            ax[0].plot(self.w/TwoPi, TwoPi*metrics['surge_PSD'][iCase,:]    )  # surge
            ax[1].plot(self.w/TwoPi, TwoPi*metrics['heave_PSD'][iCase,:]    )  # heave
            ax[2].plot(self.w/TwoPi, TwoPi*metrics['pitch_PSD'][iCase,:]    )  # pitch [deg]
            ax[3].plot(self.w/TwoPi, TwoPi*metrics['AxRNA_PSD'][iCase,:]    )  # nacelle acceleration
            ax[4].plot(self.w/TwoPi, TwoPi*metrics['Mbase_PSD'][iCase,:]    )  # tower base bending moment (using FAST's kN-m)
            ax[5].plot(self.w/TwoPi, TwoPi*metrics['wave_PSD' ][iCase,:], label=f'case {iCase+1}')  # wave spectrum

            # need a variable number of subplots for the mooring lines
            #ax2[3].plot(model.w/2/np.pi, TwoPi*metrics['Tmoor_PSD'][0,3,:]  )  # fairlead tension

        ax[0].set_ylabel('surge \n'+r'(m$^2$/Hz)')
        ax[1].set_ylabel('heave \n'+r'(m$^2$/Hz)')
        ax[2].set_ylabel('pitch \n'+r'(deg$^2$/Hz)')
        ax[3].set_ylabel('nac. acc. \n'+r'((m/s$^2$)$^2$/Hz)')
        ax[4].set_ylabel('twr. bend \n'+r'((Nm)$^2$/Hz)')
        ax[5].set_ylabel('wave elev.\n'+r'(m$^2$/Hz)')

        #ax[0].set_ylim([0.0, 25])
        #ax[1].set_ylim([0.0, 15])
        #ax[2].set_ylim([0.0, 4])
        #ax[-1].set_xlim([0.03, 0.15])
        ax[-1].set_xlabel('frequency (Hz)')
        
        #if nCases > 1:
        ax[-1].legend()
        fig.suptitle('RAFT power spectral densities')
        
        

    def preprocess_HAMS(self, dw=0, wMax=0, dz=0, da=0):
        '''This generates a mesh for the platform, runs a BEM analysis on it
        using pyHAMS, and writes .1 and .3 output files for use with OpenFAST.
        The input parameters are useful for multifidelity applications where 
        different levels have different accuracy demands for the HAMS analysis.
        The mesh is only made for non-interesecting members flagged with potMod=1.
        
        PARAMETERS
        ----------
        dw : float
            Optional specification of custom frequency increment (rad/s).
        wMax : float
            Optional specification of maximum frequency for BEM analysis (rad/s). Will only be
            used if it is greater than the maximum frequency used in RAFT.
        dz : float
            desired longitudinal panel size for potential flow BEM analysis (m)
        da : float
            desired azimuthal panel size for potential flow BEM analysis (m)
        '''
        
        self.fowtList[0].calcBEM(dw=dw, wMax=wMax, dz=dz, da=da)


    def plot(self, ax=None, hideGrid=False, color='k', nodes=0):
        '''plots the whole model, including FOWTs and mooring system...'''

        # for now, start the plot via the mooring system, since MoorPy doesn't yet know how to draw on other codes' plots
        #self.ms.bodyList[0].setPosition(np.zeros(6))
        #self.ms.initialize()
        
        #fig = plt.figure(figsize=(20/2.54,12/2.54))
        #ax = Axes3D(fig)

       # if axes not passed in, make a new figure
        if ax == None:    
            fig, ax = self.ms.plot(color=color)
        else:
            fig = ax.get_figure()
            self.ms.plot(ax=ax, color=color)

        # plot each FOWT
        for fowt in self.fowtList:
            fowt.plot(ax, color=color, nodes=nodes)
            
        if hideGrid:       
            ax.set_xticks([])    # Hide axes ticks
            ax.set_yticks([])
            ax.set_zticks([])     
            ax.grid(False)       # Hide grid lines
            ax.grid(b=None)
            ax.axis('off')
            ax.set_frame_on(False)
            
        return fig, ax


def runRAFT(input_file, turbine_file=""):
    '''
    This will set up and run RAFT based on a YAML input file.
    '''
    
    # open the design YAML file and parse it into a dictionary for passing to raft
    print("Loading RAFT input file: "+input_file)
    
    with open(input_file) as file:
        design = yaml.load(file, Loader=yaml.FullLoader)
    
    print(f"'{design['name']}'")
    
    
    depth = float(design['mooring']['water_depth'])
    
    # for now, turn off potMod in the design dictionary to avoid BEM analysis
    #design['platform']['potModMaster'] = 1
    
    # read in turbine data and combine it in
    # if len(turbine_file) > 0:
    #   turbine = convertIEAturbineYAML2RAFT(turbine_file)
    #   design['turbine'].update(turbine)
    
    # Create and run the model
    print(" --- making model ---")
    model = raft.Model(design)  
    print(" --- analyizing unloaded ---")
    model.analyzeUnloaded()
    print(" --- analyzing cases ---")
    model.analyzeCases()
    
    model.plot()
    
    model.plotResponses()
    
    #model.preprocess_HAMS("testHAMSoutput", dw=0.1, wMax=10)
    
    plt.show()
    
    return model
    
    
if __name__ == "__main__":
    import raft
    
    #model = runRAFT(os.path.join(raft_dir,'designs/DTU10MW.yaml'))
    model = runRAFT(os.path.join(raft_dir,'designs/VolturnUS-S.yaml'))
    #model = runRAFT(os.path.join(raft_dir,'designs/OC3spar.yaml'))
    fowt = model.fowtList[0]
