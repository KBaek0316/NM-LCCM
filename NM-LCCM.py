# -*- coding: utf-8 -*-
"""
Created on Thu Jul 11 16:06:50 2024

@author: Kwangho Baek baek0040@umn.edu
"""
#%% Setup
import os
import pandas as pd
import numpy as np
from numpy import linalg as LA
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.impute import KNNImputer
import matplotlib.pyplot as plt
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

if os.environ['USERPROFILE']=='C:\\Users\\baek0040':
    WPATH=r'C:\Users\baek0040\Documents\GitHub\NM-LCCM'
else:
    WPATH=os.path.abspath(r'C:\git\NM-LCCM')
pd.set_option('future.no_silent_downcasting', True)
os.chdir(WPATH)


device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(device)

pathattrstobeused=['aux','wt','iv','nTrans'] #available: 'nwk','wk','wt','ntiv','tiv','nTrans' ,'aux', 'iv'
#%% Data Preprocessing
def InputProcessing(surFile,pathFile,ver,convFile,imputeCols=[]):
    '''for debugging
    surFile='survey'
    pathFile='paths'
    ver=2022
    convFile='dfConv'
    imputeCols=['duration']
    '''
    dfSurveyRaw=pd.read_csv(surFile+str(ver)+r'.csv',low_memory=False, encoding='ISO 8859-1')
    dfConversion=pd.read_csv(convFile+r'.csv')
    #ver=int(pd.to_datetime(dfSurveyRaw.survey_date).dt.year.median())
    match ver:
        case 2016:
            pass
        case 2022:
            dfSurvey=dfSurveyRaw.loc[:,['id','collection_type','date_type','origin_place_type','destin_place_type',
                                     'plan_for_trip','realtime_info','do_you_drive', 'used_veh_trip',
                                     'hh_member_travel','origin_transport','destin_transport','trip_in_oppo_dir',
                                     'oppo_dir_trip_time','gender_male', 'gender_female','race_white','resident_visitor',
                                     'work_location','student_status','english_ability', 'your_age','income', 'have_disability']]
            dfSurvey.columns=['id','season','dayofweek','purO','purD','plan','realtime','candrive','cdhvusdveh',
                              'HHcomp','access','egress','oppo','oppotime','male','female','white','visitor','worktype','stu',
                              'engflu','age','income','disability']
    #use keygen to generate dfConv.csv
    keygen=dict()
    for col in dfSurvey.columns[1:]:
        elems=dfSurvey.loc[:,col].unique()
        if len(elems)<50:
            keygen[col]=elems.tolist()
    keygen=pd.Series(keygen, name='orilevel').rename_axis('field').explode().reset_index()
    keygen.to_clipboard(index=False,header=False)
    #how to deal with missing values?
    dfSurvey.fillna(value={'plan':'web','HHcomp':'0','worktype':'unemp','engflu':'1'},inplace=True)
    #refactoring some categorical variables from the survey format to model-able
    dfConversion=dfConversion.loc[(dfConversion.version==ver) & (dfConversion.step=='post'),:]
    for fld in dfConversion.loc[:,'field'].unique():
        dfSlice=dfConversion.loc[dfConversion.field==fld,['orilevel','newlevel']]
        dfSlice=pd.Series(dfSlice['newlevel'].values,index=dfSlice['orilevel'])
        dfSurvey=dfSurvey.replace({fld:dfSlice})
        try:
            dfSurvey[fld]=dfSurvey[fld].astype(float)
        except ValueError:
            pass
    #some variables are need to be defined using multiple survey responses
    dfSurvey['choicerider']='dependent'
    dfSurvey.loc[dfSurvey.candrive=='Yes','choicerider']='potentially'
    dfSurvey.loc[dfSurvey.cdhvusdveh=='Yes','choicerider']='choicerider'
    dfSurvey['nonbinary']=1
    dfSurvey.loc[(dfSurvey['male']+dfSurvey['female'])>0,'nonbinary']=0
    dfSurvey['purpose']='HB'
    dfSurvey.loc[(dfSurvey['purO']!='Home') & (dfSurvey['purD']!='Home'),'purpose']='NHB'
    dfSurvey.loc[dfSurvey.purpose=='HB','purpose']+=(dfSurvey.loc[dfSurvey.purpose=='HB','purO']+dfSurvey.loc[dfSurvey.purpose=='HB','purD']).str.replace('Home','')#.str[0]
    dfSurvey.loc[dfSurvey.purpose=='HB','purpose']='HBO' #there is one instance whose O and D are both Home
    #move on to the path preprocessing; paths retrieved from the repository SchBasedSPwithTE_Pandas
    dfPathRaw=pd.read_csv(pathFile+str(ver)+r'.csv',low_memory=False, encoding='ISO 8859-1')
    print('Among '+str(len(dfPathRaw.sid.unique())-2)+' survey respondents examined,')
    dfPath=dfPathRaw.drop(columns=['detail','cost','line','nodes','snap','elapsed','TE','hr']).dropna(subset='routes')
    print(str(len(dfPath.sid.unique()))+' respondents have at least one path identified from V-SBSP')
    dfPath=dfPath.loc[dfPath.sid.isin(dfPath.loc[dfPath.match==1,'sid'].unique())]
    dfPath['tway']=0
    dfPath['ntiv']=dfPath['iv']-dfPath['tiv']
    dfPath.loc[dfPath.tiv>dfPath.ntiv,'tway']=1
    dfPath=dfPath.loc[dfPath.sid.isin(dfPath.loc[dfPath.tway==1,'sid'])]
    dfPath['elap']=dfPath.label_t-dfPath.realDep
    #pairing starts
    dfCT=dfPath.loc[(dfPath.tway==1) & (dfPath.match==1),:] #chosen transitway
    dfCT=dfCT.loc[dfCT.groupby(['sid'])['elap'].rank(method='first')==1].reset_index(drop=True)
    dfAN=dfPath.loc[(dfPath.sid.isin(dfCT.sid)) & (dfPath.tway==0) & (dfPath.match==0),:] #alternative nontransitway
    dfAN=dfAN.loc[dfAN.groupby(['sid'])['elap'].rank(method='first')==1].reset_index(drop=True)
    dfCT=dfCT.loc[dfCT.sid.isin(dfAN.sid),:]
    dfCN=dfPath.loc[(dfPath.tway==0) & (dfPath.match==1),:] #chosen nontransitway
    dfCN=dfCN.loc[dfCN.groupby(['sid'])['elap'].rank(method='first')==1].reset_index(drop=True)
    dfAT=dfPath.loc[(dfPath.sid.isin(dfCN.sid)) & (dfPath.tway==1) & (dfPath.match==0),:] #alternative Transitway
    dfAT=dfAT.loc[dfAT.groupby(['sid'])['elap'].rank(method='first')==1].reset_index(drop=True)
    dfCN=dfCN.loc[dfCN.sid.isin(dfAT.sid),:]
    dfPath=pd.concat([dfCT,dfAN,dfCN,dfAT]).sort_values(['sid','tway']).reset_index(drop=True)
    dfPath=dfPath.loc[dfPath.sid.isin(np.union1d(np.intersect1d(dfCT.sid,dfAN.sid),np.intersect1d(dfCN.sid,dfAT.sid))),:]
    if len(dfPath.sid.unique())*2 != len(dfPath):
        raise Exception('Pairing Failed')
    #Imputing activity duration
    dfSurvey=pd.merge(dfSurvey,dfPathRaw.loc[dfPathRaw.match==1,['sid','realDep']],left_on='id',right_on='sid')
    dfSurvey['duration']=dfSurvey.oppotime-dfSurvey.realDep/60
    dfSurvey=dfSurvey.drop_duplicates('id').drop(columns=['purO','purD','candrive','cdhvusdveh','oppotime','sid','realDep']).reset_index(drop=True)
    print(dfSurvey.isnull().sum())
    catcols=['season','dayofweek','plan','access','egress','worktype','stu','choicerider','purpose']
    enc=OneHotEncoder(sparse_output=False)
    dfOnehot=pd.DataFrame( enc.fit_transform(dfSurvey[catcols]),columns=enc.get_feature_names_out())
    dfSurvey=pd.concat([dfSurvey.drop(columns=catcols),dfOnehot],axis=1,ignore_index=False)
    if len(imputeCols)>0: #to be updated
        pass #KNNImputer(n_neighbors=20,weights='distance').fit_transform(dfSurvey.drop(columns='id'))
    else:
        pass # only keep complete info?
    #hardcoding now...
    dfSurvey['duration']=KNNImputer(n_neighbors=20,weights='distance').fit_transform(dfSurvey.drop(columns='id'))[:,12]
    #final path filtering
    pathfilter=dfPath.groupby('sid').agg({'tway':'sum','match':'sum','elap':['count','min','max']}).reset_index()
    pathfilter.columns=['sid','tway','match','count','mint','maxt']
    pathfilter['compDiff']=pathfilter.maxt-pathfilter.mint
    pathfilter['compProp']=pathfilter.maxt/pathfilter.mint
    pathfilter2=pathfilter.loc[(pathfilter.compDiff<10) | ((pathfilter.compProp<1.5)) ]
    dfSurvey=dfSurvey.loc[dfSurvey.id.isin(pathfilter2.sid.unique()),:].reset_index(drop=True)
    dfPath=dfPath.loc[dfPath.sid.isin(pathfilter2.sid.unique()),:]
    len(pathfilter2)/len(pathfilter)
    print(f'{len(dfPath.sid.unique())} paths paired ({100*(1-len(pathfilter2)/len(pathfilter)):.2f}% filtered)')
    dfPath=dfPath.drop(columns=['ind','label_t','label_c','realDep','routes','elap']).rename(columns={"sid": "id"})
    dfPath['aux']=dfPath['wk']+dfPath['nwk']
    return dfSurvey, dfPath

dfSurvey, dfPath= InputProcessing('survey','paths',2022,'dfConv',imputeCols=['duration'])


def genTensors(dfSurvey,dfPath,pathcols=pathattrstobeused,dropcols=[],stdcols=[]):
    #dfMain: Tway paths #dfSub: nonTway paths
    dfMain=dfPath.loc[dfPath.tway==1,np.append(['id','match'],pathcols)]
    dfSub=dfPath.loc[dfPath.tway==0,np.append(['id','match'],pathcols)]
    if not np.all(dfMain.id.values==dfSub.id.values):
        raise Exception('organize dfPath or recheck the pairing steps')
    dfMain.iloc[:,2:]=dfMain.iloc[:,2:].to_numpy()-dfSub.iloc[:,2:].to_numpy()
    #final survey preprocessing with standardization
    dfSurvey2=dfSurvey.drop(columns=dropcols).copy()
    if len(stdcols)>0:
        scaler=StandardScaler()
        stdized=pd.DataFrame(scaler.fit_transform(dfSurvey[stdcols]),columns=stdcols)
        dfSurvey2.update(stdized)
    dfMain=pd.merge(dfSurvey2,dfMain,on='id')
    chloc=np.where(dfMain.columns=='match')[0][0]
    seg=torch.tensor(dfMain.iloc[:,1:chloc].to_numpy(),dtype=torch.float32).to(device)
    nume=torch.tensor(dfMain.iloc[:,(chloc+1):].to_numpy(),dtype=torch.float32).to(device)
    ch=torch.tensor(dfMain.iloc[:,chloc].to_numpy(),dtype=torch.float32).to(device)
    return seg, nume, ch, dfMain

segmentation_bases, numeric_attrs, y, dfIn=genTensors(dfSurvey,dfPath,
                                                     pathcols=pathattrstobeused,
                                                     dropcols=['duration'],
                                                     stdcols=['age', 'income', 'duration'])

#%% Model Definition
# Define the neural network model with latent classes
class LatentClassNN(nn.Module):
    def __init__(self, input_size, hidden_size, num_classes):
        super(LatentClassNN, self).__init__()
        self.layer1 = nn.Linear(input_size, hidden_size)
        self.layer2 = nn.Linear(hidden_size, 2*hidden_size)
        self.layer3 = nn.Linear(2*hidden_size, hidden_size)
        self.layero = nn.Linear(hidden_size, num_classes)

    def forward(self, x):
        x = F.relu(self.layer1(x))
        x = F.relu(self.layer2(x))
        x = F.relu(self.layer3(x))
        x = F.softmax(self.layero(x), dim=1)
        return x

class CombinedModel(nn.Module):
    def __init__(self, segmentation_input_size, num_classes, numeric_input_size,nnodes):
        super(CombinedModel, self).__init__()
        self.latent_class_nn = LatentClassNN(segmentation_input_size, nnodes, num_classes)
        self.beta = nn.Parameter(torch.randn(num_classes, numeric_input_size + 1))  # Including intercept

    def forward(self, segmentation_bases, numeric_attrs):
        latent_classes = self.latent_class_nn(segmentation_bases)
        batch_size = numeric_attrs.size(0)
        numeric_attrs_with_intercept = torch.cat([torch.ones(batch_size, 1).to(device), numeric_attrs], dim=1)
        beta_expanded = self.beta.unsqueeze(0).expand(batch_size, -1, -1)
        # Separate intercept and non-intercept beta values
        intercepts = beta_expanded[:, :, 0:1] #dim: nobs * nclass * 1
        non_intercepts = beta_expanded[:, :, 1:] #dim: nobs * nclass * nnumcols
        # Apply negative ReLU to enforce non-positive betas
        non_intercepts = -torch.nn.functional.relu(non_intercepts)
        #    non_intercepts = -torch.abs(non_intercepts) #deprecated
        # Concatenate intercepts and transformed non-intercepts
        constrained_beta_expanded = torch.cat([intercepts, non_intercepts], dim=2) #dim 2 because concatenate 1 and nnumcols
        # Compute logits for each class
        logits = torch.bmm(numeric_attrs_with_intercept.unsqueeze(1), constrained_beta_expanded.permute(0, 2, 1)).squeeze(1)
        # Aggregate class probabilities for the final output probability of y=1
        final_probabilities = torch.sum(latent_classes * torch.sigmoid(logits), dim=1)
        final_probabilities = torch.clamp(final_probabilities, 1e-7, 1 - 1e-7) #avoid log(0)
        return final_probabilities, latent_classes

# Training function
def train_model(segmentation_bases,nclass=2,nnodes=128,nepoch=500,lrate=0.001,l2=0.1):
    '''
    nepoch=500
    lrate=0.001
    l2=0.01
    '''
    model = CombinedModel(segmentation_bases.shape[1], nclass, numeric_attrs.shape[1],nnodes).to(device)
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=lrate)
    l2_lambda = l2  # L2 regularization strength
    losses = []  # List to store loss values
    # Training loop
    for epoch in range(nepoch):  # number of epochs
        model.train()
        optimizer.zero_grad()
        outputs,membership = model(segmentation_bases, numeric_attrs)
        loss = criterion(outputs,y)  # Reshape target to match output
        # L2 regularization only for nn part
        l2_reg = 0
        for name, param in model.named_parameters():
            if 'latent_class_nn' in name:
                l2_reg += torch.norm(param)
        loss += l2_lambda * l2_reg
        #gradient flow
        loss.backward()
        optimizer.step()
        losses.append(loss.item())  # Store the loss value
        if (epoch + 1) % 100 == 0:
            print(f'Epoch [{epoch+1}/{nepoch}], Loss: {loss.item():.4f}')
    outfin=outputs.detach().cpu().numpy()
    #outfin=np.clip(outfin,1e-7, 1 - 1e-7)
    ynp=y.detach().cpu().numpy()
    LLM=sum(ynp*np.log(outfin)+(1-ynp)*np.log(1-outfin))
    out0=1/2 #(LL(0))
    out0=ynp.mean() #(LL(Constant))
    LL0=sum(ynp*np.log(out0)+(1-ynp)*np.log(1-out0))
    rhosq=1-LLM/LL0
    print(f' ******* McFadden rho-sq value: {rhosq:.4f} *******')
    return model, losses, outputs, rhosq
#%% Model Tuning
def mTuning(filename):
    dfTune=pd.read_csv(filename)
    for row in dfTune.itertuples():
        if not np.isnan(row.rho0):
            continue
        print(row)
        num_classes = row.nclass
        i=0
        rhos=[]
        membership=0
        desired=0
        while i<row.niter:
            i+=1
            modelout, lossesout, estimates,rho = train_model(segmentation_bases,nclass=num_classes,
                                                             nnodes=row.nnodes,nepoch=row.nepoch,lrate=row.lrate,l2=row.l2)
            beta_values= modelout.beta.detach().clone().cpu().numpy()
            rhos.append(rho)
            print(beta_values)
            with torch.no_grad():
                _, member_prop = modelout(segmentation_bases, numeric_attrs)
            member_prop=pd.DataFrame(member_prop.detach().cpu().numpy().astype(float))
            member_prop['assigned']=member_prop.idxmax(axis=1)+1
            member_prop.columns=np.append(np.char.add('class',((np.arange(num_classes)+1).astype(str))),'assigned')
            assignedmean=member_prop.assigned.mean()
            if assignedmean>1.1 and assignedmean<1.9:
                membership+=1
                if beta_values[:,0].prod()<0 and all(beta_values[:,1:].flatten()<0.02) and rho>0.3:
                    desired+=1
        rhos=np.array(rhos)
        dfTune.loc[row.Index,'rho0']=sum(rhos>0)
        dfTune.loc[row.Index,'rho4']=sum(rhos>0.4)
        dfTune.loc[row.Index,'rhomax']=rhos.max()
        dfTune.loc[row.Index,'rhopmean']=rhos[rhos>0].mean()
        dfTune.loc[row.Index,'membership']=membership
        dfTune.loc[row.Index,'desired']=desired
        dfTune.loc[row.Index,'successprop']=desired/row.niter
        dfTune.to_csv('tuning.csv',index=False)
    return None
#mTuning('tuning.csv')
#accept tuning id 110 as our final model: nnodes=64,nepoch=500,lrate=0.05,l2=0.002
#%% Getting Results
def desiredModel(betas,rsq,interDiff=0.5/2.5,batanonsg=0.05/2.5,rsqcut=0.4): #sqrt(10-1)=3 maxElemExclAdj->2.5
    adjL2norm=LA.norm(betas.flatten()[betas.flatten()<betas.max()]) #delete largest elem, then L2
    intercepttest=((betas[:,0].prod()<0) or (abs(betas[0,0]-betas[1,0])>interDiff*adjL2norm))
    betatest=all(betas[:,1:].flatten()<batanonsg*adjL2norm)
    rhotest=rsq>rsqcut
    testResult=intercepttest*rhotest*betatest
    print(f'intercept: {intercepttest}, coeffs: {betatest}, rho: {rhotest}')
    return testResult

modelout=0 #initialize
num_classes=2
desired=0
i=1
betanames=np.array([[f"{b}_{i}" for b in np.append('ASC',pathattrstobeused)] for i in range(1, num_classes + 1)])

try:
    del modelout
except:
    pass
while desired<100:
    modelout, lossesout, estimates,rho = train_model(
        segmentation_bases,nclass=num_classes,nnodes=64,nepoch=500,lrate=0.05,l2=0.002)
    beta_values= modelout.beta.detach().clone().cpu().numpy()
    print(f"Estimated beta values with the format {betanames}:")
    print(beta_values)
    if  desiredModel(beta_values,rho):
        # Plot the loss values
        plt.plot(range(1, len(lossesout) + 1), lossesout)
        plt.xlabel('Epoch')
        plt.ylabel('Loss')
        plt.title(f'Training Loss for model {i}')
        plt.show()
        #get probs
        modelout.eval()
        with torch.no_grad():
            _, member_prop = modelout(segmentation_bases, numeric_attrs)
        member_prop=pd.DataFrame(member_prop.detach().cpu().numpy().astype(float))
        member_prop['assigned']=member_prop.idxmax(axis=1)+1
        member_prop.columns=np.append(np.char.add('class',((np.arange(num_classes)+1).astype(str))),'assigned')
        assignedmean=member_prop.assigned.mean()
        if assignedmean>1.1 and assignedmean<1.9:
            LA.norm(beta_values.flatten()[beta_values.flatten()<beta_values.max()]) # for inspection
            desired+=1
            print('!!!!!!!!!!!!!!!!!!!!!!!!Desired model found!!!!!!!!!!!!!!!!!!!!!!')
            if beta_values[0,0]<beta_values[1,0]: #Make class 1 as transitway likely class
                beta_values=beta_values[[1,0],:] #swap rows
                member_prop['assigned']=(3-member_prop.assigned) #invert 1 and 2
                member_prop.rename(columns={"class2": "class1","class1": "class2"},inplace=True) #swap cols
            storeit=pd.Series(np.append(np.append(rho,beta_values.flatten()),member_prop['class1']),
                              index=np.append(np.append('rhosq',betanames.flatten()),dfIn.id))
            if desired==1:
                dataOut=pd.DataFrame({('Model'+str(i)):storeit})
            else:
                dataOut[('Model'+str(i))]=storeit
            dataOut.to_csv('modelout.csv')
    i+=1
print('Finished')

#%%
def genChoicedf(dfSurvey,dfPath):
    pass




'''deprecated
class CombinedModelD(nn.Module):
    def __init__(self, segmentation_input_size, num_classes, numeric_input_size):
        super(CombinedModelD, self).__init__()
        self.latent_class_nn = LatentClassNN(segmentation_input_size, 32, num_classes)
        self.beta = nn.Parameter(torch.randn(num_classes, numeric_input_size + 1))  # Including intercept
    def forward(self, segmentation_bases, numeric_attrs):
        latent_classes = self.latent_class_nn(segmentation_bases)
        batch_size = numeric_attrs.size(0)
        numeric_attrs_with_intercept = torch.cat([torch.ones(batch_size, 1).to(device), numeric_attrs], dim=1)
       # Determine the most probable latent class for each observation
        most_probable_classes = torch.argmax(latent_classes, dim=1)
        # Create a tensor to hold the logits
        logits = torch.zeros(batch_size).to(device)
        # Compute logits for the most probable class for each observation
        for i in range(batch_size):
            class_index = most_probable_classes[i]
            beta_values = self.beta[class_index]
            # Separate intercept and non-intercept beta values
            intercept = beta_values[0]
            non_intercepts = beta_values[1:]
            # Apply transformation to enforce non-positive non-intercept beta values
            non_intercepts = -torch.nn.functional.relu(non_intercepts)
            # Compute the logit for the current observation
            logit = intercept + torch.dot(non_intercepts, numeric_attrs_with_intercept[i, 1:])
            logits[i] = logit
        # Aggregate class probabilities for the final output probability of y=1
        final_probabilities = torch.sigmoid(logits)
        # Clamp the probabilities to avoid log(0) issues
        final_probabilities = torch.clamp(final_probabilities, 1e-7, 1 - 1e-7)
        return final_probabilities
# Training function
def train_modelD(segmentation_bases,nepoch=300,lrate=0.001,l2=0.1):
    model = CombinedModelD(segmentation_bases.shape[1], num_classes, numeric_attrs.shape[1]).to(device)
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=lrate)
    l2_lambda = l2  # L2 regularization strength
    losses = []  # List to store loss values
    # Training loop
    for epoch in range(nepoch):  # number of epochs
        model.train()
        optimizer.zero_grad()
        outputs = model(segmentation_bases, numeric_attrs)
        loss = criterion(outputs,y)  # Reshape target to match output
        # L2 regularization only for nn part
        l2_reg = 0
        for name, param in model.named_parameters():
            if 'latent_class_nn' in name:
                l2_reg += torch.norm(param)
        loss += l2_lambda * l2_reg
        #gradient flow
        loss.backward()
        optimizer.step()
        losses.append(loss.item())  # Store the loss value
        if (epoch + 1) % 50 == 0:
            print(f'Epoch [{epoch+1}/{nepoch}], Loss: {loss.item():.4f}')
    outfin=outputs.detach().cpu().numpy()
    ynp=np.clip(y.detach().cpu().numpy(),1e-7, 1 - 1e-7)
    #comp=pd.DataFrame([ynp,outfin]).T
    #comp['sqerr']=(comp.loc[:,0]-comp.loc[:,1])**2
    LLM=sum(ynp*np.log(outfin)+(1-ynp)*np.log(1-outfin))
    #out0=ynp.mean()
    out0=1/2
    LL0=sum(ynp*np.log(out0)+(1-ynp)*np.log(1-out0))
    rhosq=1-LLM/LL0
    print(f' ******* McFadden rho-sq value: {rhosq:.4f} *******')
    return model, losses, outputs

# Train models for both encodings
num_classes = 2  # Define the number of latent classes
modelout, lossesout, estimates = train_modelD(segmentation_bases,nepoch=300,lrate=0.001,l2=0.1)

# Plot the loss values
plt.plot(range(1, len(lossesout) + 1), lossesout)
plt.xlabel('Epoch')
plt.ylabel('Loss')
plt.title('Training Loss')
plt.show()

# Inspect the estimated beta values for both models
beta_values= modelout.beta.detach().clone().cpu().numpy()
print("Estimated beta values:")
print(beta_values)
'''
