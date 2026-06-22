import pandas as pd
from ortools.sat.python import cp_model

import json
import os

def generer_planning(indisponibilites, rules, target_week=None, previous_grid=None, locked_shifts=None):
    # Chargement des règles strictes depuis regles.json
    regles_stricte = {
        "effectif_lundi_ideal": 5,
        "effectif_lundi_min": 4,
        "effectif_lundi_max": 5,
        "effectif_semaine_ideal": 6,
        "effectif_semaine_min": 4,
        "effectif_semaine_max": 6,
        "effectif_samedi_ideal": 4,
        "effectif_samedi_min": 3,
        "effectif_samedi_max": 4,
        "effectif_dimanche_exact": 2,
        "repos_semaine_min": 2,
        "repos_semaine_max": 2,
        "stabilite_types_ideal": 1,
        "poids_couverture": 5000,
        "poids_capacite": 1000,
        "poids_stabilite": 100,
        "poids_priorite": 5,
        "poids_ricardo_deviation": 4000,
        "max_jours_travail_semaine": 5
    }
    try:
        regles_path = os.path.join(os.path.dirname(__file__), 'regles.json')
        if os.path.exists(regles_path):
            with open(regles_path, 'r', encoding='utf-8') as f:
                regles_stricte.update(json.load(f))
    except Exception as e:
        print("Erreur chargement regles.json:", e)

    # ==========================================
    # 1. DONNÉES ET CONFIGURATION
    # ==========================================
    agents = ['RICARDO', 'FLORIAN', 'CAMILO', 'GUILLAUME', 'JP', 'MARIOLA', 'ROBIN']
    tournants = agents[1:]
    
    num_days = 42  # Cycle de 6 semaines
    num_weeks = 6
    
    # Définition des types de shifts
    SHIFTS_NOMS = ['REPOS', 'Matin', 'J1', 'J2', 'Soir', 'Soir_Dim', 'Senior']
    SHIFT_R = 0
    SHIFT_M = 1
    SHIFT_J1 = 2
    SHIFT_J2 = 3
    SHIFT_S = 4
    SHIFT_SD = 5
    SHIFT_JS = 6
    
    # ==========================================
    # 2. MODÉLISATION CP-SAT
    # ==========================================
    model = cp_model.CpModel()
    
    # Variables de décision principales
    # shifts[a, d] : type de shift assigné à l'agent 'a' au jour 'd'
    shifts = {}
    for a in agents:
        for d in range(num_days):
            shifts[(a, d)] = model.NewIntVar(0, 6, f'shift_{a}_{d}')
            
    # Variables booléennes associées (pour simplifier l'écriture des contraintes logiques)
    # shift_bool[a, d, s] vaut 1 si l'agent 'a' fait le shift 's' le jour 'd'
    shift_bool = {}
    for a in agents:
        for d in range(num_days):
            for s in range(7):
                b = model.NewBoolVar(f'shift_bool_{a}_{d}_{s}')
                shift_bool[(a, d, s)] = b
                model.Add(shifts[(a, d)] == s).OnlyEnforceIf(b)
                model.Add(shifts[(a, d)] != s).OnlyEnforceIf(b.Not())
    # Liste globale de toutes les variables de pénalité (slack)
    all_penalties = []
    coverage_penalties = []
    stability_penalties = []
    other_penalties = []
    weekend_slacks = {}
                
    # --- C4 : Contraintes pour RICARDO (Senior) ---
    ricardo_penalties = []
    if True: # Toujours actif
        for d in range(num_days):
            slack_ricardo = model.NewIntVar(0, 1, f'slack_ricardo_{d}')
            if d % 7 < 5:  # Lundi (0) au Vendredi (4) - Idéal: JS
                model.Add(shift_bool[('RICARDO', d, SHIFT_JS)] + slack_ricardo == 1)
            else:          # Samedi (5) et Dimanche (6) - Idéal: REPOS
                model.Add(shift_bool[('RICARDO', d, SHIFT_R)] + slack_ricardo == 1)
            ricardo_penalties.append(slack_ricardo)
            all_penalties.append(slack_ricardo)
            
    # --- Contraintes générales pour les Tournants ---
    for a in tournants:
        for d in range(num_days):
            # JS n'est plus strictement réservé, Ricardo est juste prioritaire dessus.
            # model.Add(shifts[(a, d)] != SHIFT_JS)  <- retiré            
            # Gestion des shifts du soir selon le jour de la semaine
            if d % 7 == 6: 
                # Le dimanche, pas de "Soir" normal
                model.Add(shifts[(a, d)] != SHIFT_S)
            else:          
                # Du lundi au samedi, pas de "Soir_Dim"
                model.Add(shifts[(a, d)] != SHIFT_SD)
                
    # --- C2 : Temps de repos minimum de 11 heures ---
    # Interdiction des enchaînements impossibles du jour J au jour J+1
    # Exempt pour le Senior s'il dépanne occasionnellement, on l'applique uniquement aux tournants
    if rules.get('toggle-repos', True):
        for a in tournants:
            for d in range(num_days - 1):
                model.AddForbiddenAssignments([shifts[(a, d)], shifts[(a, d+1)]], [
                    (SHIFT_S, SHIFT_M),
                    (SHIFT_SD, SHIFT_M)
                ])
            
    # --- NOUVELLE C3 : Repos consécutifs (Fenêtre glissante) ---
    for a in tournants:
        # 1. Limitation : Repos minimum et maximum élastiques (incluant les absences)
        for w in range(num_weeks):
            abs_count = sum(1 for d in range(7) if (w*7 + d) in indisponibilites.get(a, []))
            jours_repos = sum(shift_bool[(a, w*7 + d, SHIFT_R)] for d in range(7))
            
            min_repos = min(7, regles_stricte["repos_semaine_min"] + abs_count)
            max_repos = min(7, regles_stricte["repos_semaine_max"] + abs_count)
            
            slack_repos = model.NewIntVar(0, 7, f'slack_repos_{a}_{w}')
            model.Add(jours_repos >= min_repos - slack_repos)
            model.Add(jours_repos <= max_repos + slack_repos)
            
            all_penalties.append(slack_repos)
            # Pénalité très forte (équivalente à la couverture) pour éviter de casser les jours de repos sauf urgence absolue
            for _ in range(5):
                other_penalties.append(slack_repos)
            
        # 2. Obligation que chaque REPOS soit collé à un autre REPOS (élastique, autorisé en cas d'urgence)
        for d in range(num_days):
            repos_aujourdhui = shift_bool[(a, d, SHIFT_R)]
            slack_repos_isole = model.NewIntVar(0, 1, f'slack_repos_isole_{a}_{d}')
            
            # Cas particuliers : premier et dernier jour du cycle
            if d == 0:
                repos_demain = shift_bool[(a, d+1, SHIFT_R)]
                model.Add(repos_demain + slack_repos_isole >= 1).OnlyEnforceIf(repos_aujourdhui)
            elif d == num_days - 1:
                repos_hier = shift_bool[(a, d-1, SHIFT_R)]
                model.Add(repos_hier + slack_repos_isole >= 1).OnlyEnforceIf(repos_aujourdhui)
            else:
                repos_hier = shift_bool[(a, d-1, SHIFT_R)]
                repos_demain = shift_bool[(a, d+1, SHIFT_R)]
                
                # Mathématiquement : si repos_aujourdhui est vrai, alors (repos_hier + repos_demain + slack_repos_isole >= 1)
                model.Add(repos_hier + repos_demain + slack_repos_isole >= 1).OnlyEnforceIf(repos_aujourdhui)
                
            # Pénalité forte pour éviter le repos isolé, sauf cas d'urgence absolue
            all_penalties.append(slack_repos_isole)
            for _ in range(5):
                other_penalties.append(slack_repos_isole)
            
    # --- C8 : Gestion des Indisponibilités (Absences, Congés) ---
    for a, jours in indisponibilites.items():
        if a in agents:
            for d in jours:
                if 0 <= d < num_days:
                    model.Add(shifts[(a, d)] == SHIFT_R)
                
    # --- FIXATION DES SEMAINES (si on recalcule une seule semaine) ---
    if target_week is not None and previous_grid is not None:
        for a in agents:
            if a in previous_grid:
                prev_shifts = previous_grid[a]
                for d in range(num_days):
                    if d // 7 != target_week and d < len(prev_shifts):
                        shift_name = prev_shifts[d]
                        if shift_name == 'ABSENT':
                            model.Add(shifts[(a, d)] == SHIFT_R)
                        elif shift_name in SHIFTS_NOMS:
                            model.Add(shifts[(a, d)] == SHIFTS_NOMS.index(shift_name))

    # --- FIXATION DES SHIFTS VERROUILLÉS INDIVIDUELLEMENT ---
    if locked_shifts:
        for a, locks in locked_shifts.items():
            if a in agents:
                for d_str, shift_name in locks.items():
                    d = int(d_str)
                    if 0 <= d < num_days:
                        if shift_name == 'ABSENT':
                            model.Add(shifts[(a, d)] == SHIFT_R)
                        elif shift_name in SHIFTS_NOMS:
                            model.Add(shifts[(a, d)] == SHIFTS_NOMS.index(shift_name))



    # --- C7 : Stabilité (Minimiser les types de shifts par agent par semaine) ---
    # Objectif idéal : 1 seul type de shift sur la semaine. Tout type supplémentaire ajoute du slack pénalisé.
    for a in tournants:
        for w in range(num_weeks):
            used_m = model.NewBoolVar(f'used_{a}_{w}_m')
            used_j1 = model.NewBoolVar(f'used_{a}_{w}_j1')
            used_j2 = model.NewBoolVar(f'used_{a}_{w}_j2')
            # On regroupe Soir et Soir_Dim sous le même type logique de stabilité
            used_s = model.NewBoolVar(f'used_{a}_{w}_s')
            
            for d in range(7):
                model.AddImplication(shift_bool[(a, w*7+d, SHIFT_M)], used_m)
                model.AddImplication(shift_bool[(a, w*7+d, SHIFT_J1)], used_j1)
                model.AddImplication(shift_bool[(a, w*7+d, SHIFT_J2)], used_j2)
                model.AddImplication(shift_bool[(a, w*7+d, SHIFT_S)], used_s)
                model.AddImplication(shift_bool[(a, w*7+d, SHIFT_SD)], used_s)
                
            # Flexibilité maximale : la baseline est issue de regles_stricte, tout surplus est un écart (slack)
            slack_stabilite = model.NewIntVar(0, 4, f'slack_stabilite_{a}_{w}')
            model.Add(used_m + used_j1 + used_j2 + used_s <= regles_stricte["stabilite_types_ideal"] + slack_stabilite)
            all_penalties.append(slack_stabilite)
            stability_penalties.append(slack_stabilite)
            
    # --- NOUVELLE C9 : Composition stricte des effectifs le Week-end ---
    if True: # Composition stricte toujours active
        for d in range(num_days):
            if d % 7 == 5: # Samedi
                slack_sam_matin = model.NewIntVar(0, 1, f'slack_sam_matin_{d}')
                model.Add(sum(shift_bool[(a, d, SHIFT_M)] for a in agents) + slack_sam_matin >= 1)
                all_penalties.append(slack_sam_matin)
                coverage_penalties.append(slack_sam_matin)
                weekend_slacks[(d, 'Matin')] = slack_sam_matin

                slack_sam_soir = model.NewIntVar(0, 1, f'slack_sam_soir_{d}')
                model.Add(sum(shift_bool[(a, d, SHIFT_S)] for a in agents) + slack_sam_soir >= 1)
                all_penalties.append(slack_sam_soir)
                coverage_penalties.append(slack_sam_soir)
                weekend_slacks[(d, 'Soir')] = slack_sam_soir

                slack_sam_j1 = model.NewIntVar(0, 1, f'slack_sam_j1_{d}')
                model.Add(sum(shift_bool[(a, d, SHIFT_J1)] for a in agents) + slack_sam_j1 >= 1)
                all_penalties.append(slack_sam_j1)
                coverage_penalties.append(slack_sam_j1)
                weekend_slacks[(d, 'J1')] = slack_sam_j1

                slack_sam_j2 = model.NewIntVar(0, 1, f'slack_sam_j2_{d}')
                model.Add(sum(shift_bool[(a, d, SHIFT_J2)] for a in agents) + slack_sam_j2 >= 1)
                all_penalties.append(slack_sam_j2)
                coverage_penalties.append(slack_sam_j2)
                weekend_slacks[(d, 'J2')] = slack_sam_j2

            elif d % 7 == 6: # Dimanche
                slack_dim_matin = model.NewIntVar(0, 1, f'slack_dim_matin_{d}')
                model.Add(sum(shift_bool[(a, d, SHIFT_M)] for a in agents) + slack_dim_matin >= 1)
                all_penalties.append(slack_dim_matin)
                coverage_penalties.append(slack_dim_matin)
                weekend_slacks[(d, 'Matin')] = slack_dim_matin

                slack_dim_soir = model.NewIntVar(0, 1, f'slack_dim_soir_{d}')
                model.Add(sum(shift_bool[(a, d, SHIFT_SD)] for a in agents) + slack_dim_soir >= 1)
                all_penalties.append(slack_dim_soir)
                coverage_penalties.append(slack_dim_soir)
                weekend_slacks[(d, 'Soir_Dim')] = slack_dim_soir
            
    # --- NOUVELLE C10 : Capacité Globale des Tournants et Senior ---
    capa_slacks = {}
    for d in range(num_days):
        working_agents = sum(shift_bool[(a, d, s)] for a in agents for s in range(1, 7))
        extreme_slack = model.NewIntVar(0, 10, f'extreme_slack_capa_{d}')
        
        if d % 7 == 0: # Lundi
            slack_capa_lun = model.NewIntVar(0, 6, f'slack_capa_lun_{d}')
            model.Add(working_agents + slack_capa_lun == regles_stricte["effectif_lundi_ideal"])
            model.Add(working_agents <= regles_stricte["effectif_lundi_max"] + extreme_slack)
            model.Add(working_agents >= regles_stricte["effectif_lundi_min"] - extreme_slack)
            all_penalties.append(slack_capa_lun)
            other_penalties.append(slack_capa_lun)
            capa_slacks[d] = ('Lundi', slack_capa_lun, regles_stricte["effectif_lundi_ideal"])
        elif d % 7 in [1, 2, 3, 4]: # Mardi au Vendredi
            slack_capa_sem = model.NewIntVar(0, 6, f'slack_capa_sem_{d}')
            model.Add(working_agents + slack_capa_sem == regles_stricte["effectif_semaine_ideal"])
            model.Add(working_agents <= regles_stricte["effectif_semaine_max"] + extreme_slack)
            model.Add(working_agents >= regles_stricte["effectif_semaine_min"] - extreme_slack)
            all_penalties.append(slack_capa_sem)
            other_penalties.append(slack_capa_sem)
            capa_slacks[d] = ('Semaine', slack_capa_sem, regles_stricte["effectif_semaine_ideal"])
        elif d % 7 == 5: # Samedi
            slack_capa_sam = model.NewIntVar(0, 6, f'slack_capa_sam_{d}')
            model.Add(working_agents + slack_capa_sam == regles_stricte["effectif_samedi_ideal"])
            model.Add(working_agents <= regles_stricte["effectif_samedi_max"] + extreme_slack)
            model.Add(working_agents >= regles_stricte["effectif_samedi_min"] - extreme_slack)
            all_penalties.append(slack_capa_sam)
            other_penalties.append(slack_capa_sam)
            capa_slacks[d] = ('Samedi', slack_capa_sam, regles_stricte["effectif_samedi_ideal"])
        elif d % 7 == 6: # Dimanche
            slack_capa_dim = model.NewIntVar(0, 6, f'slack_capa_dim_{d}')
            model.Add(working_agents + slack_capa_dim == regles_stricte["effectif_dimanche_exact"])
            model.Add(working_agents <= regles_stricte["effectif_dimanche_exact"] + extreme_slack)
            model.Add(working_agents >= regles_stricte["effectif_dimanche_exact"] - extreme_slack)
            
        # Pénalité extrême pour tout dépassement des bornes min/max dures
        all_penalties.append(extreme_slack)
        for _ in range(10):
            other_penalties.append(extreme_slack)

    # --- NOUVELLE RÈGLE : Maximum d'agents sur les shifts d'extrémité (7h et 15h) ---
    for d in range(num_days):
        model.Add(sum(shift_bool[(a, d, SHIFT_M)] for a in agents) <= regles_stricte.get("max_matin", 1))
        
        if d % 7 == 6: # Dimanche
            model.Add(sum(shift_bool[(a, d, SHIFT_SD)] for a in agents) <= regles_stricte.get("max_soir", 1))
        else:
            model.Add(sum(shift_bool[(a, d, SHIFT_S)] for a in agents) <= regles_stricte.get("max_soir", 1))

    # --- NOUVELLE RÈGLE : Interdit de travailler 6 jours dans une semaine ---
    for a in agents:
        for w in range(num_weeks):
            jours_travail_semaine = sum(shift_bool[(a, w*7 + d, s)] for d in range(7) for s in range(1, 7))
            model.Add(jours_travail_semaine <= regles_stricte.get("max_jours_travail_semaine", 5))

    # --- NOUVELLE RÈGLE : Équité de répartition des shifts Matin et Soir ---
    # Calcul des jours de présence par agent tournant
    P_a = {}
    for a in tournants:
        abs_count = sum(1 for d in range(num_days) if d in indisponibilites.get(a, []))
        P_a[a] = num_days - abs_count
    
    P_total = sum(P_a.values()) if sum(P_a.values()) > 0 else 1
    
    equite_slacks = []
    for a in tournants:
        # 42 shifts Matin et 42 shifts Soir au total sur le cycle
        target_matin = round(num_days * (P_a[a] / P_total))
        target_soir = round(num_days * (P_a[a] / P_total))
        
        total_matin_a = sum(shift_bool[(a, d, SHIFT_M)] for d in range(num_days))
        total_soir_a = sum(shift_bool[(a, d, SHIFT_S)] for d in range(num_days) if d % 7 < 6) + \
                       sum(shift_bool[(a, d, SHIFT_SD)] for d in range(num_days) if d % 7 == 6)
                       
        slack_equite_m = model.NewIntVar(0, num_days, f'slack_equite_m_{a}')
        model.Add(total_matin_a - target_matin <= slack_equite_m)
        model.Add(target_matin - total_matin_a <= slack_equite_m)
        
        slack_equite_s = model.NewIntVar(0, num_days, f'slack_equite_s_{a}')
        model.Add(total_soir_a - target_soir <= slack_equite_s)
        model.Add(target_soir - total_soir_a <= slack_equite_s)
        
        equite_slacks.append((a, 'Matin', slack_equite_m, target_matin))
        equite_slacks.append((a, 'Soir', slack_equite_s, target_soir))
        
        # Le poids de l'équité est appliqué
        weight_equite = regles_stricte.get("poids_equite", 500)
        # On simule un poids de X en ajoutant X fois la variable à la liste all_penalties 
        # (Attention : si le poids est 500 on ajoute pas 500 fois, on gère les poids plus bas)
        # Actuellement le système minimise sum(all_penalties). Si on veut ajouter un coefficient :
        # Le plus simple dans l'architecture actuelle est de multiplier l'impact de ce slack dans l'objectif global.
        # Mais sum(all_penalties) est flat. Alors on va l'ajouter weight_equite fois à other_penalties.
        # Ou bien on le rajoute 10 fois pour que ça pèse "10" par rapport à 1.
        # On va l'ajouter N fois dans all_penalties et other_penalties. N = weight_equite // 50 par exemple.
        multiplier = weight_equite // 50
        for _ in range(multiplier):
            all_penalties.append(slack_equite_m)
            all_penalties.append(slack_equite_s)
            other_penalties.append(slack_equite_m)
            other_penalties.append(slack_equite_s)

    # --- C5 & C6 : Couverture Élastique (Autorisée à échouer en force majeure) ---
    missing_Matin = []
    missing_Soir = []
    missing_J1 = []
    missing_J2 = []
    
    for d in range(num_days):
        if d % 7 < 5:  # Du Lundi au Vendredi uniquement
            # Matin
            m_matin = model.NewIntVar(0, 1, f'missing_matin_{d}')
            model.Add(sum(shift_bool[(a, d, SHIFT_M)] for a in agents) + m_matin >= 1)
            missing_Matin.append(m_matin)
            all_penalties.append(m_matin)
            coverage_penalties.append(m_matin)
            
            # Soir
            m_soir = model.NewIntVar(0, 1, f'missing_soir_{d}')
            model.Add(sum(shift_bool[(a, d, SHIFT_S)] for a in agents) + m_soir >= 1)
            missing_Soir.append(m_soir)
            all_penalties.append(m_soir)
            coverage_penalties.append(m_soir)
            
            # J1
            m_j1 = model.NewIntVar(0, 1, f'missing_j1_{d}')
            model.Add(sum(shift_bool[(a, d, SHIFT_J1)] for a in agents) + m_j1 >= 1)
            missing_J1.append(m_j1)
            all_penalties.append(m_j1)
            coverage_penalties.append(m_j1)
            
            # J2
            m_j2 = model.NewIntVar(0, 1, f'missing_j2_{d}')
            model.Add(sum(shift_bool[(a, d, SHIFT_J2)] for a in agents) + m_j2 >= 1)
            missing_J2.append(m_j2)
            all_penalties.append(m_j2)
            coverage_penalties.append(m_j2)
        else:
            missing_Matin.append(None)
            missing_Soir.append(None)
            missing_J1.append(None)
            missing_J2.append(None)
            
    # ==========================================
    # 3. FONCTION OBJECTIF
    # ==========================================
    poids_prio = regles_stricte["poids_priorite"]
    priority_score = []
    for d in range(num_days):
        if d % 7 < 5:  # Optimisation en semaine
            for a in agents:
                # Pondération prioritaire pour J1 et Senior
                priority_score.append(poids_prio * shift_bool[(a, d, SHIFT_J1)])
                priority_score.append(poids_prio * shift_bool[(a, d, SHIFT_JS)])
                # Pondération neutre (0) pour les autres
                
    total_priority = sum(priority_score)
    
    poids_couv = regles_stricte["poids_couverture"]
    poids_capa = regles_stricte["poids_capacite"]
    poids_stab = regles_stricte["poids_stabilite"]
    poids_ric = regles_stricte["poids_ricardo_deviation"]
    
    # Maximiser les shifts prioritaires de façon inconditionnelle
    model.Maximize(total_priority - poids_couv * sum(coverage_penalties) - poids_capa * sum(other_penalties) - poids_stab * sum(stability_penalties) - poids_ric * sum(ricardo_penalties))
    
    # ==========================================
    # 4. RÉSOLUTION ET EXPORT
    # ==========================================
    solver = cp_model.CpSolver()
    # Limiter le temps de recherche (60 secondes)
    solver.parameters.max_time_in_seconds = 60.0
    solver.parameters.search_branching = cp_model.FIXED_SEARCH
    
    status = solver.Solve(model)
    
    result = {
        "status": solver.StatusName(status),
        "grid": {},
        "alerts": [],
        "score_total": 0,
        "total_penalties": 0,
        "penalties_details": [],
        "health_score": 100
    }
    
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        result["score_total"] = solver.ObjectiveValue()
        result["total_penalties"] = solver.Value(sum(all_penalties))
        
        # --- Calcul des pénalités détaillées ---
        jours_semaine = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
        
        for d in range(num_days):
            jour_nom = jours_semaine[d % 7]
            semaine = 25 + (d // 7)
            
            if d % 7 < 5:
                if missing_Matin[d] is not None and solver.Value(missing_Matin[d]) > 0:
                    result["penalties_details"].append({"message": f"Matin non couvert le {jour_nom} (S{semaine})", "day_index": d, "gravity": "Critique"})
                if missing_Soir[d] is not None and solver.Value(missing_Soir[d]) > 0:
                    result["penalties_details"].append({"message": f"Soir non couvert le {jour_nom} (S{semaine})", "day_index": d, "gravity": "Critique"})
                if missing_J1[d] is not None and solver.Value(missing_J1[d]) > 0:
                    result["penalties_details"].append({"message": f"J1 non couvert le {jour_nom} (S{semaine})", "day_index": d, "gravity": "Critique"})
                if missing_J2[d] is not None and solver.Value(missing_J2[d]) > 0:
                    result["penalties_details"].append({"message": f"J2 non couvert le {jour_nom} (S{semaine})", "day_index": d, "gravity": "Critique"})
            elif d % 7 == 5:
                if (d, 'Matin') in weekend_slacks and solver.Value(weekend_slacks[(d, 'Matin')]) > 0:
                    result["penalties_details"].append({"message": f"Matin non couvert le Samedi (S{semaine})", "day_index": d, "gravity": "Critique"})
                if (d, 'Soir') in weekend_slacks and solver.Value(weekend_slacks[(d, 'Soir')]) > 0:
                    result["penalties_details"].append({"message": f"Soir non couvert le Samedi (S{semaine})", "day_index": d, "gravity": "Critique"})
                if (d, 'J1') in weekend_slacks and solver.Value(weekend_slacks[(d, 'J1')]) > 0:
                    result["penalties_details"].append({"message": f"J1 non couvert le Samedi (S{semaine})", "day_index": d, "gravity": "Critique"})
                if (d, 'J2') in weekend_slacks and solver.Value(weekend_slacks[(d, 'J2')]) > 0:
                    result["penalties_details"].append({"message": f"J2 non couvert le Samedi (S{semaine})", "day_index": d, "gravity": "Critique"})
            elif d % 7 == 6:
                if (d, 'Matin') in weekend_slacks and solver.Value(weekend_slacks[(d, 'Matin')]) > 0:
                    result["penalties_details"].append({"message": f"Matin non couvert le Dimanche (S{semaine})", "day_index": d, "gravity": "Critique"})
                if (d, 'Soir_Dim') in weekend_slacks and solver.Value(weekend_slacks[(d, 'Soir_Dim')]) > 0:
                    result["penalties_details"].append({"message": f"Soir Dim. non couvert le Dimanche (S{semaine})", "day_index": d, "gravity": "Critique"})
                    
            if d in capa_slacks:
                nom_regle, slack_var, ideal = capa_slacks[d]
                if solver.Value(slack_var) > 0:
                    effectif_reel = ideal - solver.Value(slack_var)
                    result["penalties_details"].append({"message": f"Sous-effectif global ({effectif_reel} agents au lieu de {ideal}) le {jour_nom} (S{semaine})", "day_index": d, "gravity": "Avertissement"})
                    
        for (a, periode, slack_var, target) in equite_slacks:
            val = solver.Value(slack_var)
            if val > 1: # Tolérance de +/- 1 shift par rapport à la moyenne parfaite
                result["penalties_details"].append({"message": f"Déséquilibre ({val} shifts d'écart avec la moyenne) sur les {periode}s pour {a}", "day_index": -1, "gravity": "Avertissement"})
                    
        total_contraintes_elastiques = len(all_penalties)
        if total_contraintes_elastiques > 0:
            result["health_score"] = max(0, int(100 - (result["total_penalties"] / total_contraintes_elastiques * 100)))

        for a in agents:
            row = []
            absences = indisponibilites.get(a, [])
            for d in range(num_days):
                s = solver.Value(shifts[(a, d)])
                if d in absences:
                    row.append("ABSENT")
                else:
                    row.append(SHIFTS_NOMS[s])
            result["grid"][a] = row
            
        # Ligne spécifique pour alerter le manager sur les sous-effectifs
        for d in range(num_days):
            alerts = []
            if d % 7 < 5:
                if solver.Value(missing_Matin[d]) > 0:
                    alerts.append("Vide Matin")
                if solver.Value(missing_Soir[d]) > 0:
                    alerts.append("Vide Soir")
                if solver.Value(missing_J1[d]) > 0:
                    alerts.append("Vide J1")
                if solver.Value(missing_J2[d]) > 0:
                    alerts.append("Vide J2")
            result["alerts"].append(" / ".join(alerts) if alerts else "")
            
    return result

if __name__ == '__main__':
    # Test local
    print(generer_planning({'FLORIAN': [1, 2]}, {'toggle-ricardo': True, 'slider-stabilite': 2, 'toggle-weekend': True, 'toggle-priorite': True}))
