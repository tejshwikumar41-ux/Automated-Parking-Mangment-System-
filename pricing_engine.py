"""
Dynamic Pricing Engine for Parking Management System
Implements time-based, occupancy-based, and event-based pricing strategies
"""

from datetime import datetime
from typing import Dict, Any
import math

class DynamicPricingEngine:
    def __init__(self, db_connection):
        self.db = db_connection
        self.base_rate = 20.0  # ₹20/hour fallback base rate
        
        # Dynamically fetch the hourly rate from the active pricing rule
        try:
            cursor = self.db.cursor()
            cursor.execute("SELECT hourly_rate FROM pricing_rules WHERE is_active = 1 LIMIT 1")
            row = cursor.fetchone()
            if row:
                try:
                    self.base_rate = float(row['hourly_rate'])
                except (TypeError, KeyError, IndexError):
                    self.base_rate = float(row[0])
        except Exception:
            pass
        
    def calculate_dynamic_rate(
        self, 
        entry_time: datetime, 
        vehicle_type: str = "STANDARD",
        occupancy_rate: float = 0.0
    ) -> Dict[str, Any]:
        """
        Calculate dynamic parking rate based on multiple factors
        """
        rate = self.base_rate
        multipliers = {}
        
        # 1. PEAK HOUR MULTIPLIER (9-11 AM, 5-7 PM)
        hour = entry_time.hour
        if hour in [9, 10, 17, 18]:
            multipliers['peak_hour'] = 1.5
        elif hour in [11, 12, 13, 14, 15, 16]:
            multipliers['peak_hour'] = 1.2  # Moderate demand
        else:
            multipliers['peak_hour'] = 1.0
            
        # 2. OCCUPANCY SURGE PRICING
        if occupancy_rate >= 0.9:
            multipliers['occupancy'] = 1.5  # Nearly full - premium pricing
        elif occupancy_rate >= 0.8:
            multipliers['occupancy'] = 1.3
        elif occupancy_rate >= 0.6:
            multipliers['occupancy'] = 1.1
        else:
            multipliers['occupancy'] = 1.0
            
        # 3. WEEKEND/HOLIDAY PREMIUM
        if entry_time.weekday() >= 5:  # Saturday=5, Sunday=6
            multipliers['weekend'] = 1.2
        else:
            multipliers['weekend'] = 1.0
            
        # 4. VEHICLE TYPE MODIFIER
        vehicle_multipliers = {
            "STANDARD": 1.0,
            "VIP": 0.8,      # Discount for VIP members
            "EV": 1.3,       # EV charging premium
            "OVERSIZED": 1.5  # Trucks/SUVs take more space
        }
        multipliers['vehicle_type'] = vehicle_multipliers.get(vehicle_type, 1.0)
        
        # CALCULATE FINAL RATE
        final_multiplier = 1.0
        for key, value in multipliers.items():
            final_multiplier *= value
            
        final_rate = round(rate * final_multiplier, 2)
        
        # Generate human-readable breakdown
        breakdown_parts = [f"₹{rate} base"]
        for key, value in multipliers.items():
            if value != 1.0:
                breakdown_parts.append(f"{value}x {key.replace('_', ' ')}")
        breakdown = " x ".join(breakdown_parts)
        
        return {
            "base_rate": rate,
            "multipliers": multipliers,
            "final_rate": final_rate,
            "breakdown": breakdown,
            "timestamp": entry_time.isoformat()
        }
    
    def calculate_total_fee(
        self, 
        entry_time: datetime, 
        exit_time: datetime,
        vehicle_type: str = "STANDARD"
    ) -> Dict[str, Any]:
        """
        Calculate total parking fee with dynamic pricing
        """
        # Get current occupancy
        occupancy = self._get_current_occupancy()
        
        # Get dynamic rate at entry time
        pricing = self.calculate_dynamic_rate(entry_time, vehicle_type, occupancy)
        hourly_rate = pricing['final_rate']
        
        # Calculate duration
        duration_seconds = (exit_time - entry_time).total_seconds()
        duration_hours = duration_seconds / 3600
        
        # Apply free grace period (first 15 minutes free)
        if duration_seconds < 900:  # 15 minutes
            return {
                "duration_minutes": round(duration_seconds / 60, 1),
                "total_fee": 0.0,
                "pricing_breakdown": pricing,
                "note": "Free grace period (< 15 minutes)"
            }
        
        # Calculate fee with minimum 1 hour charge
        billable_hours = max(1.0, math.ceil(duration_hours))
        total_fee = round(hourly_rate * billable_hours, 2)
        
        return {
            "entry_time": entry_time.isoformat(),
            "exit_time": exit_time.isoformat(),
            "duration_minutes": round(duration_seconds / 60, 1),
            "duration_hours": round(duration_hours, 2),
            "billable_hours": billable_hours,
            "hourly_rate": hourly_rate,
            "total_fee": total_fee,
            "pricing_breakdown": pricing
        }
    
    def _get_current_occupancy(self) -> float:
        """Calculate current occupancy percentage"""
        try:
            cursor = self.db.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(CASE WHEN status = 'OCCUPIED' THEN 1 END) as occupied,
                    COUNT(*) as total
                FROM parking_slots
            """)
            row = cursor.fetchone()
            if not row:
                return 0.0
            try:
                occupied = row['occupied']
                total = row['total']
            except (TypeError, KeyError, IndexError):
                occupied = row[0]
                total = row[1]
                
            if total == 0:
                return 0.0
            return float(occupied) / float(total)
        except Exception:
            return 0.0
    
    def get_pricing_forecast(self, hours_ahead: int = 24) -> list:
        """
        Predict pricing for next N hours (useful for customer planning)
        """
        from datetime import timedelta
        
        forecast = []
        current_time = datetime.now()
        
        for hour in range(hours_ahead):
            future_time = current_time + timedelta(hours=hour)
            # Use historical average occupancy for that hour
            avg_occupancy = self._get_historical_occupancy(future_time.hour)
            
            pricing = self.calculate_dynamic_rate(
                future_time, 
                "STANDARD", 
                avg_occupancy
            )
            
            forecast.append({
                "hour": future_time.strftime("%I %p"),
                "rate": pricing['final_rate'],
                "multiplier": round(pricing['final_rate'] / self.base_rate, 2)
            })
        
        return forecast
    
    def _get_historical_occupancy(self, hour: int) -> float:
        """Get average occupancy for a specific hour based on historical data"""
        # Simulate realistic diurnal patterns
        peak_hours = {9: 0.85, 10: 0.9, 17: 0.88, 18: 0.92, 11: 0.7, 12: 0.65, 13: 0.6, 14: 0.65, 15: 0.7, 16: 0.75}
        return peak_hours.get(hour, 0.45)
