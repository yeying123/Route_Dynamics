""" Implementation of the Longitudinal Dynamics Model for work done by
    the bus engine along route.

    From:
        Asamer J, Graser A, Heilmann B, Ruthmair M. Sensitivity
        analysis for energy demand estimation of electric vehicles.
        Transportation Research Part D: Transport and Environment.
        2016 Jul 1;46:182-99.

    This file contains one main class, which constructs the route DataFrame
    consisting of rows corresponding to points along the route
    """

# from ..route_elevation import single_route as rsr
from ..route_elevation import base as re_base
from . import knn
from . import constant_a as ca

import numpy as np
import geopandas as gpd


class IllegalArgumentError(ValueError):
    """ """
    pass


class PlottingTools(object):
    """ Place holder for now, but eventually this will wrap up the
        plotting tools written by last quarter's RouteDynamics team.
        """

    def __init__(self):
        pass


# Thinking this is not the best implementation since I don't actually
# know how to make objects print like pandas DataFrames.
class RouteTrajectory(PlottingTools):
    """ Takes 2d route coordinates extracted from shapefile and
        combines the information with elevation to create a route
        trajectory dataframe.
        """

    def __init__(self,
        route_num,
        shp_filename,
        elv_raster_filename,
        bus_speed_model='stopped_at_stops__15mph_between',
        stop_coords=None,
        mass_array=None,
        unloaded_bus_mass=12927,
        charging_power_max=0., # should be kW
        # charging_power_max=50000 # should be kW
        a_m=1.0,
        v_lim=15.0,
        ):
        """ Build DataFrame with bus trajectory and shapely connections
            for plotting. This object is mostly a wrapper object to
            build and return the Route DataFrame, but will later
            contain plotting methods as well.


            Args:

                route_num: needs to be one that Erica made work.

                bus_speed_model: has options;
                    - 'stopped_at_stops__15mph_between'
                    - 'constant_15mph'
                    - 'const_accel_between_stops_and_speed_lim'

            Methods:

                ...

            """

        self._initialize_instance_args(
            bus_speed_model,
            a_m,
            v_lim,
            stop_coords,
            mass_array,
            unloaded_bus_mass,
            charging_power_max,
            )

        # Build Route DataFrame, starting with columns:
        #     - 'elevation'
        #     - 'cum_distance'
        #     - 'is_bus_stop
        self.route_df = self.build_route_coordinate_df(
            route_num = route_num,
            shp_filename = shp_filename,
            elv_raster_filename = elv_raster_filename,
            )

        self.route_df = self._add_dynamics_to_df(
            route_df=self.route_df,
            stop_coords=stop_coords,
            bus_speed_model=self.bus_speed_model,
            )


    def _initialize_instance_args(self,
        bus_speed_model,
        a_m,
        v_lim,
        stop_coords,
        mass_array,
        unloaded_bus_mass,
        charging_power_max,
        ):

        # Store algorithm name for future reference.
        self.bus_speed_model = bus_speed_model

        # default speed limit and acceleration constant
        self.a_m = a_m
        self.v_lim = v_lim


        self.stop_coords = stop_coords

        # Mass stuff
        self.mass_array = mass_array
        self.unloaded_bus_mass = unloaded_bus_mass

        # Boolean check for instance argument 'mass_array'
        self.mass_arg_is_list = (
            type(self.mass_array) is list
            or
            type(self.mass_array) is np.ndarray
            )
        ####

        # Store chargeing ability as instance attribute
        self.charging_power_max = charging_power_max


    def _add_dynamics_to_df(self,
        route_df,
        stop_coords,
        bus_speed_model,
        ):

        # Try to determine bus stops from list of coordinates
        route_df = self._add_stops_to_df(stop_coords, route_df)

        # Depending on the method of bus speed estimation, the next
        # block of code will exicute in different orders
        if bus_speed_model in [
            'constant_15mph',
            'stopped_at_stops__15mph_between'
            ]:
            # Add 'velocity' column to route_df first
            # This will also involve calulating the velocity.
            route_df = self._add_velocities_to_df(
                route_df,
                bus_speed_model=bus_speed_model,
                )

            route_df = self._add_delta_times_to_df(route_df)

            # Add 'acceleration' column to route_df, calculated as
            # finite difference from velocities
            route_df = self._add_accelerations_to_df(
                route_df,
                alg='finite_diff',
                )


        elif bus_speed_model in [
            'const_accel_between_stops_and_speed_lim'
            ]:

            # Add 'acceleration' column to route_df
            route_df = self._add_accelerations_to_df(
                route_df,
                alg='const_accel_between_stops_and_speed_lim',
                )

            route_df = self._add_velocities_to_df(
                route_df,
                bus_speed_model='const_accel_between_stops_and_speed_lim',
                )

            route_df = self._add_delta_times_to_df(route_df, 'model')

        # Add passenger mass column to route_df
        route_df = self._add_mass_to_df(route_df)

        # Add force columns to route_df:
        #     - 'grav_force' : gravitation force determined by road grade
        #     - 'roll_fric' : rolling friction
        #     - 'aero_drag' : areodynamic drag
        #     - 'inertia' : inertial force, F = ma. Changes with passenger load
        #                   on bus.
        route_df = self._add_forces_to_df(route_df)

        # Add column to route_df containing instantaneous power experted by
        # bus at each point along route.
        route_df = self._add_power_to_df(route_df)

        return route_df


    def build_route_coordinate_df(self,
        route_num,
        shp_filename,
        elv_raster_filename,
        ):
        """ Builds GeoDataFrame with rows cooresponding to points on
            route with columns corresponding to elevation, elevation
            gradiant, and connecting line segments between points in
            the form of Shapely Linstring objects.

            Also adds bus stop column and assigns bus stops based on
            'stop_coords' argument

            Args:
                'stop_coords': list of coordinates of bus stops. Will
                    assign points along bus route based on these values
                    .

            """

        # Build the df of 2D route coordinates and
        route_shp = re_base.read_shape(shp_filename, route_num)

        # print(f'route_shp: {route_shp}\n')

        route_2Dcoord_df = re_base.extract_point_df(route_shp)

        # print(f'elv_raster_filename: {elv_raster_filename}\n')

        (
            elevation,
            elevation_gradient,
            route_cum_distance,
            back_diff_distance
            ) = re_base.gradient(route_shp, elv_raster_filename)

        route_df = re_base.make_multi_lines(
            route_2Dcoord_df,
            elevation_gradient
            )


        route_df = self._add_distance_to_df(back_diff_distance, route_df)

        route_df = self._add_elevation_to_df(elevation, route_df)

        route_df = self._add_cum_dist_to_df(route_cum_distance, route_df)

        return route_df


    def _add_distance_to_df(self, back_diff_distance, route_df):

        distance_array = np.append(np.nan,back_diff_distance)

        rdf = route_df.assign(
            distance_from_last_point=distance_array
            )
        return rdf

    def _add_stops_to_df(self, stop_coords, route_df):
        """ Find rows in route_df matching the stop_coordinates and
            mark as bus stop under new column.
            """

        # By default, 'stop_coords' is set to 'None', if this is true,
        # then 10 bus stops will be assigned randomly
        if stop_coords is 'random':
            # Randomly select certain route coordinates to be marked as
            # a stop with 5% probability.
            # Fix seed for reproducability
            np.random.seed(5615423)
            # Return binary array with value 'True' 5% of time
            is_stop__truth_array = (
                np.random.random(len(route_df.index)) < .15
                )

            route_df = route_df.assign(
                is_bus_stop = is_stop__truth_array
                )

        elif stop_coords is None:
            # Mark no stops
            route_df = route_df.assign(
                is_bus_stop = ([False] * len(route_df.index))
                )

        elif (type(stop_coords) is list) or (type(stop_coords) is np.ndarray):

            # Calculate indicies of 'stop_coords' that match bus_stops
            self.stop_nn_indicies, self.stop_coord_nn = knn.find_knn(
                1,
                route_df.coordinates.values,
                stop_coords
                )
            # the 'jth' element of stop_nn_indicies also selects the

            route_df = route_df.assign(
                is_bus_stop = ([False] * len(route_df.index))
                )

            for i in self.stop_nn_indicies.ravel():
                route_df.at[i, 'is_bus_stop'] = True


        else:
            raise IllegalArgumentError(
                "'stop_coords' must be 'random', None, "
                "or type(list)/type(ndarray)"
                )

        # route_df.at[0, 'is_bus_stop'] = True
        # route_df.at[-1, 'is_bus_stop'] = True

        return route_df


    def _add_elevation_to_df(self, elevation, route_df):

        # print(len(elevation), len(route_df.index))
        # print('elevation', elevation)

        rdf = route_df.assign(
            elevation=elevation.ravel()
            )



        return rdf


    def _add_cum_dist_to_df(self, cum_distance, route_df):

        rdf = route_df.assign(
            cum_distance=cum_distance
            )

        return rdf


    def _add_velocities_to_df(self, route_df, bus_speed_model):
        """ For now just adds a constant velocity as a placeholder.
            """

        lazy_choise_for_speed = 6.7056  # 6.7056 m/s (= 15 mph)

        # 'test' algorithm set by default for now.
        if bus_speed_model == 'constant_15mph':
            # Assign constant velocity
            bus_speed_array = (
                lazy_choise_for_speed * np.ones(len(route_df.index))
                )

        elif bus_speed_model == 'stopped_at_stops__15mph_between':
            # Really I want something here to use the stop array to calcularte bus speed.
            # Step !: Calculate distance to next stop, which should determine the strajectory (speed at point)
                # can use difference of 'cum_dist's
            # 2) Assign trajectory as function of distance
            # 3) plug in each route point between stops intor trajectory function.
            # ... This is all UNDER CONSTRUCTION ...

            # Right now, this will just make stop points have zero velocity.
            zero_if_stop__one_if_not = (
                np.logical_not(route_df.is_bus_stop.values)*1
                )

            # Mark endpoints of route as well
            zero_if_stop_start_end__one_if_not = zero_if_stop__one_if_not
            zero_if_stop_start_end__one_if_not[0] = 0
            zero_if_stop_start_end__one_if_not[-1] = 0

            # if not stop, set velocity to 15 mph
            bus_speed_array = zero_if_stop__one_if_not * lazy_choise_for_speed


        elif bus_speed_model is 'const_accel_between_stops_and_speed_lim':
            bus_speed_array = self.const_a_velocities

        rdf = route_df.assign(
            velocity=bus_speed_array
            )

        return rdf


    def _add_delta_times_to_df(self, route_df, alg='finite_diff'):
        """ Add delta_times for finite_difference calculation of acceleration """

        if alg is 'finite_diff':
            delta_times = self._calculate_delta_times_on_linestring_distance(
                route_df)
        elif alg is 'model':
            delta_times = np.append(
                0,
                np.diff(self.route_time)
                )

        rdf = route_df.assign(
            delta_time=delta_times
            )

        return rdf


    def _calculate_delta_times_on_linestring_distance(self,
        route_df,
        alg='finite_diff',
        ):

        back_diff_delta_x = route_df.distance_from_last_point.values

        try:
            velocities = route_df.velocity.values
        except AttributeError:
            print("Does 'route_df' have 'velocity' column? ")

        if alg is 'finite_diff':
            # Calcule average velocities along segment but backward difference
            segment_avg_velocities = (
                velocities
                +
                np.append(0,velocities[:-1])
                )/2

            self.delta_times = back_diff_delta_x * segment_avg_velocities

        else:
            raise IllegalArgumentError("time calculation only equiped to "
                "implement finite difference.")


        self.time_on_route = np.append(
            0,
            np.cumsum(self.delta_times[1:])
            )

        return self.delta_times


    def _add_accelerations_to_df(self, route_df, alg='finite_diff'):
        """ For now just adds a acceleration velocity as a placeholder.
            """
        # print(route_df.head())
        accelerations = self._calculate_acceleration(route_df, alg)

        #Assign acceleration values to new row in route DataFrame.
        rdf = route_df.assign(
            acceleration=accelerations
            )

        return rdf


    def _calculate_acceleration(self,
        route_df,
        alg='finite_diff',
        a_m=None,
        v_lim=None,
        ):

        # Calculate acceleration
        if alg=='finite_diff':
            # Use finite difference of velocities to calculate accelerations
            velocity_array = route_df.velocity.values

            delta_distance_array = route_df.distance_from_last_point.values

            # assert (np.shape(np.diff(velocity_array))==np.shape(delta_distance_array)), (
            #     "np.shape(np.diff(velocity_array) = {}\n"
            #     "np.shape(delta_distance_array) = {}\n".format(
            #         np.shape(np.diff(velocity_array)),
            #         np.shape(delta_distance_array)
            #         )
            #     )

            # Calculate acceleraetion by central difference

            zero_in_a_list = np.array([0])

            back_diff_velocity_array = np.append(
                zero_in_a_list,
                np.diff(velocity_array)
                )

            # Assign backward diff velocities as instance attribute
            self.delta_v = back_diff_velocity_array

            # forward_diff_velocity_array = np.append(
            #     np.diff(velocity_array),
            #     zero_in_a_list
            #     )

            # central_diff_velocity_array = (
            #     back_diff_velocity_array
            #     +
            #     forward_diff_velocity_array
            #     )/2.

            # But average acceleration cooresponding to the linestring
            # distance will be the backward difference in velovity...
            # divided by time and not distance...

            dt = route_df.delta_time.values

            accelerations = np.append(
                np.nan,
                self.delta_v[1:] / dt[1:]
                )

        elif alg=='const_accel_between_stops_and_speed_lim':

            if v_lim is None: v_lim=self.v_lim
            if a_m is None: a_m=self.a_m

            (
                accelerations,
                self.const_a_velocities,
                self.x_ls,
                self.x_ns,
                self.route_time
                ) = ca.const_a_dynamics(
                route_df,
                a_m,
                v_lim,
                )

        else:
            raise IllegalArgumentError((
                "'alg' keywarg must be implemented algorithm. "
                "Currently supported are; \n"
                "    - 'finite_diff' : calculates finite difference in"
                " velocities and distances and takes the ratio.\n"
                "and nothing else... maybe one day it will have an analytic"
                " option."
                ))

        return accelerations


    def _add_mass_to_df(self,
        route_df,
        ):
        """ Compute number of passengers along the route.

            Eventually this will use Ryan's ridership module, which
            determines the ridership at each bus stop.
            """
        if self.mass_arg_is_list:

            lengths_equiv = len(self.mass_array)==len(
                self.stop_coords)
            # Does mass array check out for calculation?
            mass_array_correct_length = (
                lengths_equiv and self.mass_arg_is_list
                )

            full_mass_column = self.calculate_mass(
                alg='list_per_stop',
                len_check=mass_array_correct_length
                )

        else: # Add default mass to every row
            full_mass_column = self.unloaded_bus_mass*np.ones(
                len(route_df.index))


        route_df = route_df.assign(
            mass = full_mass_column
            )

        return route_df


    def calculate_mass(self,
        alg='list_per_stop',
        len_check=None,
        ):
        """ Take mass array that is length of bus stop array and store
            as df column with interpolated values in between stops
            (value from last stop). If no mass array was input as class
            arg, then default bus mass is stored in every df row.
            """


        if alg=='list_per_stop' and len_check:

            if not hasattr(self, 'stop_nn_indicies'):
                raise AttributeError('Cant calculate from list')


            # Initialize array of Nan's for mass column of rdf
            full_mass_column = np.zeros(len(self.route_df.index))
            full_mass_column[:] = np.nan

            # Iterate through the length of the given mass_array
            # (already determined equal length to 'stop_coords').
            for i in range(len(self.mass_array)):
                # Set values of mass at bus_stops
                full_mass_column[
                    self.stop_nn_indicies[i]
                    ] = self.mass_array[i]

            # Set initial and value to unloaded bus mass.
            full_mass_column[0] = self.unloaded_bus_mass
            full_mass_column[-1] = self.unloaded_bus_mass

            # Iterate through the half constructed rdf mass column
            # ('full_mass_column') and fill in sapce between stops with previous value
            for i in range(len(full_mass_column)-1):
                j = 1
                try:
                    while np.isnan(full_mass_column[i+j]):
                        full_mass_column[i+j] = full_mass_column[i]
                        # print(full_mass_column[i+j] )
                        j+=1
                except: IndexError

            if np.any(full_mass_column < self.unloaded_bus_mass):
                raise IllegalArgumentError("Class arg 'unloaded_bus_mass' "
                    "is heavier than values in arg 'mass_array'")

        elif alg=='list_per_stop' and (
            self.mass_arg_is_list and not len_check
            ):
            raise IllegalArgumentError(
                "'stop_coords' and 'mass_array' must be same length"
                )

        else:
            raise IllegalArgumentError(
                "Algorithm for mass calculation must be 'list_per_stop'"
                )


        return full_mass_column


    def _add_forces_to_df(self, route_df):
        """ Calculate forces on bus relevant to the Longitudinate
            dynamics model.
            """

        (
            grav_force,
            roll_fric,
            aero_drag,
            inertia
            ) = self.calculate_forces(route_df)

        route_df = route_df.assign(
            grav_force = grav_force,
            roll_fric = roll_fric,
            aero_drag = aero_drag,
            inertia = inertia,
            )

        return route_df


    def calculate_forces(self, rdf):
        """ Requires GeoDataFrame input with mass column """

        vels = rdf.velocity.values
        acce = rdf.acceleration.values
        grad = rdf.gradient.values
        grad_angle = np.arctan(grad)


        # Physical parameters
        gravi_accel = 9.81
        air_density = 1.225 # air density in kg/m3; consant for now,
            # eventaully input from weather API
        v_wind = 0.0 # wind speed in km per hour; figure out component,
            # and also will come from weather API
        fric_coeff = 0.01

        # List of Bus Parameters for 40 foot bus
        if self.mass_array is None:
            loaded_bus_mass = self.unloaded_bus_mass # Mass of bus in kg
        else:
            loaded_bus_mass = rdf.mass.values

        width = 2.6 # in m
        height = 3.3 # in m
        bus_front_area = width * height
        drag_coeff = 0.34 # drag coefficient estimate from paper (???)
        rw = 0.28575 # radius of wheel in m


        # Calculate the gravitational force
        grav_force = -(
            loaded_bus_mass * gravi_accel * np.sin(grad_angle)
            )

        # Calculate the rolling friction
        roll_fric = -(
            fric_coeff * loaded_bus_mass * gravi_accel * np.cos(grad_angle)
            )

        # Calculate the aerodynamic drag
        aero_drag = -(
            drag_coeff
            *
            bus_front_area
            *
            (air_density/2)
            *
            (vels-v_wind)
            )

        # Calculate the inertial force
        inertia = loaded_bus_mass * acce

        return (grav_force, roll_fric, aero_drag, inertia)


    def _calculate_batt_power_exert(self, rdf):

        f_resist = (
            rdf.grav_force.values
            +
            rdf.roll_fric.values
            +
            rdf.aero_drag.values
            )

        f_traction = rdf.inertia.values - f_resist

        velocity = rdf.velocity.values

        # calculate raw power before capping charging ability of bus
        batt_power_exert = f_traction * velocity
        self.raw_batt_power_exert = np.copy(batt_power_exert)

        for i in range(len(batt_power_exert)):
            if batt_power_exert[i] < -self.charging_power_max:
                batt_power_exert[i] = -self.charging_power_max

        return batt_power_exert


    def _add_power_to_df(self, rdf):

        batt_power_exert = self._calculate_batt_power_exert(rdf)

        new_df = rdf.assign(
            power_output = batt_power_exert
            )

        return new_df


    def energy_from_route(self):

        rdf = self.route_df

        delta_t = rdf.delta_time.values[1:]

        power = rdf.power_output.values[1:]

        energy = np.sum(power * delta_t)

        return energy
